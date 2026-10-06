"""Smoke checks for the local phone controller API."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from web_app import create_app
except ImportError:
    TestClient = None

from control_service import ControlService, SettingsStore


@unittest.skipIf(TestClient is None, "install requirements-web.txt and httpx for API checks")
class WebAppTests(unittest.TestCase):
    def test_state_scene_frame_and_hardware_settings(self):
        with TemporaryDirectory() as directory:
            service = ControlService(store=SettingsStore(Path(directory) / "control.db"))
            service.start()
            try:
                with TestClient(create_app(service)) as client:
                    self.assertEqual(client.get("/").status_code, 200)
                    self.assertEqual(client.get("/api/state").json()["scene"], "clock")
                    scene = client.put("/api/scene", json={"scene": "circle"})
                    self.assertEqual(scene.status_code, 200)
                    self.assertEqual(scene.json()["scene"], "circle")
                    image = client.get("/api/frame.png")
                    self.assertEqual(image.status_code, 200)
                    self.assertEqual(image.headers["content-type"], "image/png")
                    self.assertEqual(image.headers["cache-control"], "no-store")
                    self.assertEqual(client.put("/api/brightness", json={"brightness": 42}).json()["brightness"], 42)
                    slowdown = client.put("/api/gpio-slowdown", json={"gpio_slowdown": 3})
                    self.assertTrue(slowdown.json()["restart_required"])
                    self.assertEqual(client.put("/api/scene", json={"scene": "invalid"}).status_code, 400)
                    self.assertEqual(client.put("/api/brightness", json={"brightness": 0}).status_code, 400)
                    with patch("headless_scene.configured_client_id", return_value=""):
                        self.assertEqual(client.put("/api/scene", json={"scene": "spotify"}).status_code, 409)
                    self.assertEqual(client.get("/api/state").json()["scene"], "circle")
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()
