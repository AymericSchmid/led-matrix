"""Smoke checks for the local phone controller API."""

import base64
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

try:
    from fastapi.testclient import TestClient
    from web_app import create_app
except ImportError:
    TestClient = None

from control_service import ControlService, SettingsStore


CLIENT_ID = "0123456789abcdef0123456789abcdef"


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

    def test_phone_spotify_login_rejects_forged_callback_and_saves_token(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = ControlService(
                store=SettingsStore(root / "control.db"), spotify_data_dir=root / ".spotify",
            )
            service.start()
            try:
                with TestClient(create_app(service)) as client:
                    start = client.post("/api/spotify/login/start", json={"client_id": CLIENT_ID})
                    self.assertEqual(start.status_code, 200)
                    query = parse_qs(urlsplit(start.json()["url"]).query)
                    self.assertEqual(query["client_id"], [CLIENT_ID])
                    self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:8888/callback"])
                    self.assertEqual(query["code_challenge_method"], ["S256"])
                    self.assertNotIn("code_verifier", query)
                    self.assertEqual(SettingsStore(root / "control.db").load().spotify_client_id, CLIENT_ID)
                    callback = f"http://127.0.0.1:8888/callback?code=sample&state={query['state'][0]}"
                    forged = callback.replace(query["state"][0], "wrong")
                    self.assertEqual(client.post("/api/spotify/login/complete", json={"callback_url": forged}).status_code, 400)
                    self.assertTrue(client.get("/api/spotify/status").json()["pending"])
                    with patch("spotify_client.request_json", return_value={
                        "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
                    }) as exchange:
                        result = client.post("/api/spotify/login/complete", json={"callback_url": callback})
                    self.assertEqual(result.status_code, 200)
                    self.assertTrue(result.json()["connected"])
                    self.assertFalse(result.json()["pending"])
                    self.assertNotIn("access", str(result.json()))
                    self.assertEqual(exchange.call_args.kwargs["fields"]["code"], "sample")
                    verifier = exchange.call_args.kwargs["fields"]["code_verifier"]
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                    self.assertEqual(query["code_challenge"], [challenge])
                    self.assertTrue((root / ".spotify" / "tokens.json").exists())
                    self.assertEqual(client.delete("/api/spotify/connection").status_code, 200)
                    self.assertFalse(client.get("/api/spotify/status").json()["connected"])
                    self.assertFalse((root / ".spotify" / "tokens.json").exists())
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()
