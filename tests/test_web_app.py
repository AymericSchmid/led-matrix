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
from spotify_client import DEFAULT_PHONE_REDIRECT_URI


CLIENT_ID = "0123456789abcdef0123456789abcdef"


@unittest.skipIf(TestClient is None, "install requirements-web.txt and httpx for API checks")
class WebAppTests(unittest.TestCase):
    def setUp(self):
        for target, value in (
            ("control_service.configured_env_client_id", ""),
            ("control_service.phone_redirect_uri", DEFAULT_PHONE_REDIRECT_URI),
        ):
            active = patch(target, return_value=value)
            active.start()
            self.addCleanup(active.stop)

    def test_state_scene_frame_and_hardware_settings(self):
        with TemporaryDirectory() as directory:
            service = ControlService(store=SettingsStore(Path(directory) / "control.db"))
            service.start()
            try:
                with TestClient(create_app(service)) as client:
                    page = client.get("/")
                    self.assertEqual(page.status_code, 200)
                    self.assertIn('id="clock-theme"', page.text)
                    self.assertIn('id="spotify-style"', page.text)
                    self.assertIn('id="weather-mode"', page.text)
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
                    options = client.put("/api/scene-options", json={
                        "scene": "clock", "changes": {"theme": "dark", "hand_length_percent": 120},
                    })
                    self.assertEqual(options.status_code, 200)
                    self.assertEqual(options.json()["theme"], "dark")
                    self.assertEqual(client.get("/api/state").json()["hand_length_percent"], 120)
                    invalid = client.put("/api/scene-options", json={
                        "scene": "spotify", "changes": {"theme": "light"},
                    })
                    self.assertEqual(invalid.status_code, 400)
                    self.assertEqual(client.get("/api/state").json()["theme"], "dark")
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

    def test_github_pages_callback_returns_to_local_controller(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = ControlService(
                store=SettingsStore(root / "control.db"), spotify_data_dir=root / ".spotify",
            )
            service.start()
            try:
                with TestClient(create_app(service)) as client:
                    start = client.post("/api/spotify/login/start", json={
                        "client_id": CLIENT_ID, "return_origin": "http://matrix.local:8000",
                    })
                    self.assertEqual(start.status_code, 200)
                    query = parse_qs(urlsplit(start.json()["url"]).query)
                    self.assertEqual(query["redirect_uri"], [DEFAULT_PHONE_REDIRECT_URI])
                    self.assertIn(".", query["state"][0])
                    self.assertEqual(client.get("/spotify/callback", params={
                        "code": "sample", "state": "wrong",
                    }).status_code, 400)
                    with patch("spotify_client.request_json", return_value={
                        "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
                    }) as exchange:
                        callback = client.get("/spotify/callback", params={
                            "code": "sample", "state": query["state"][0],
                        }, follow_redirects=False)
                    self.assertEqual(callback.status_code, 303)
                    self.assertEqual(callback.headers["location"], "/?spotify=connected")
                    self.assertEqual(exchange.call_args.kwargs["fields"]["redirect_uri"], DEFAULT_PHONE_REDIRECT_URI)
                    self.assertTrue(client.get("/api/spotify/status").json()["connected"])
            finally:
                service.stop()

    def test_bridge_rejects_nonlocal_return_address(self):
        with TemporaryDirectory() as directory:
            service = ControlService(store=SettingsStore(Path(directory) / "control.db"))
            service.start()
            try:
                with TestClient(create_app(service)) as client:
                    response = client.post("/api/spotify/login/start", json={
                        "client_id": CLIENT_ID, "return_origin": "http://example.com:8000",
                    })
                    self.assertEqual(response.status_code, 400)
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()
