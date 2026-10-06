"""The phone preview and matrix controls share persistent scene state."""

from io import BytesIO
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from control_service import ControlService, SettingsStore
from spotify_client import SpotifyError
from weather_scene import weather_kind


class ControlServiceTests(unittest.TestCase):
    def setUp(self):
        active = patch("control_service.configured_env_client_id", return_value="")
        active.start()
        self.addCleanup(active.stop)

    def test_expired_spotify_login_cannot_exchange_code(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = ControlService(
                store=SettingsStore(root / "control.db"), spotify_data_dir=root / ".spotify",
            )
            try:
                url = service.start_spotify_login("0123456789abcdef0123456789abcdef")["url"]
                state = parse_qs(urlsplit(url).query)["state"][0]
                service.spotify_login = replace(service.spotify_login, expires_at=time.monotonic() - 1)
                callback = f"http://127.0.0.1:8888/callback?code=sample&state={state}"
                with self.assertRaises(SpotifyError):
                    service.finish_spotify_login(callback)
                self.assertFalse((root / ".spotify" / "tokens.json").exists())
            finally:
                service.stop()

    def test_client_id_from_environment_is_used_and_locked(self):
        with TemporaryDirectory() as directory:
            with patch("control_service.configured_env_client_id", return_value="0123456789abcdef0123456789abcdef"):
                service = ControlService(store=SettingsStore(Path(directory) / "control.db"))
            try:
                status = service.spotify_status()
                self.assertEqual(status["client_id"], "0123456789abcdef0123456789abcdef")
                self.assertTrue(status["client_id_locked"])
                with self.assertRaises(SpotifyError):
                    service.start_spotify_login("a" * 32)
            finally:
                service.stop()

    def test_scene_preview_and_settings_survive_restart(self):
        with TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "control.db")
            service = ControlService(store=store)
            service.start()
            try:
                service.select_scene("moon")
                deadline = time.monotonic() + 2
                while not service.state()["caption"].startswith("Moon") and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(service.state()["caption"].startswith("Moon"))
                service.select_scene("circle")
                service.set_brightness(42)
                service.set_gpio_slowdown(3)
                deadline = time.monotonic() + 2
                while service.state()["caption"] != "Circle" and time.monotonic() < deadline:
                    time.sleep(0.02)
                frame = Image.open(BytesIO(service.frame_png()))
                self.assertEqual(frame.size, (64, 64))
                self.assertNotEqual(frame.getpixel((11, 31)), (0, 0, 0))
                self.assertEqual(service.state()["caption"], "Circle")
                self.assertTrue(service.state()["restart_required"])
                self.assertNotIn("spotify_client_id", service.state())
                with self.assertRaises(ValueError):
                    service.select_scene("not-a-scene")
            finally:
                service.stop()

            restarted = ControlService(store=store)
            try:
                self.assertEqual(restarted.state()["scene"], "circle")
                self.assertEqual(restarted.state()["brightness"], 42)
                self.assertEqual(restarted.state()["gpio_slowdown"], 3)
                self.assertFalse(restarted.state()["restart_required"])
            finally:
                restarted.stop()

    def test_scene_options_update_live_and_survive_restart(self):
        with TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "control.db")
            service = ControlService(store=store)
            try:
                before = service.source.frame()[0].tobytes()
                service.set_scene_options("clock", {
                    "theme": "dark", "no_numbers": True,
                    "hand_length_percent": 125, "hand_width_percent": 150,
                })
                after = service.source.frame()[0].tobytes()
                self.assertNotEqual(before, after)
                self.assertEqual(service.state()["hand_width_percent"], 150)
                service.set_scene_options("spotify", {
                    "spotify_style": "colour-drift", "no_drift_cover": True,
                    "drift_blackness": 5,
                })
                service.set_scene_options("weather", {
                    "weather_demo": "snow", "weather_hour": 23,
                    "weather_city": "Fribourg",
                })
                service.select_scene("weather")
                self.assertIsNone(service.source.weather_worker)
                self.assertEqual(service.source.args.weather_hour, 23)
                service.set_scene_options("weather", {"weather_demo": "rain"})
                self.assertEqual(weather_kind(service.source.weather.code), "rain")
            finally:
                service.stop()

            restarted = ControlService(store=store)
            try:
                self.assertEqual(restarted.state()["theme"], "dark")
                self.assertEqual(restarted.state()["spotify_style"], "colour-drift")
                self.assertEqual(restarted.state()["weather_demo"], "rain")
            finally:
                restarted.stop()

    def test_scene_options_reject_invalid_values_without_mutating_settings(self):
        with TemporaryDirectory() as directory:
            service = ControlService(store=SettingsStore(Path(directory) / "control.db"))
            try:
                before = service.state()
                for scene, changes in (
                    ("clock", {"theme": "blue"}),
                    ("clock", {"hand_length_percent": 140}),
                    ("clock", {"no_ticks": 1}),
                    ("weather", {"weather_hour": 24}),
                    ("weather", {"weather_city": " "}),
                    ("spotify", {"drift_blackness": -1}),
                    ("moon", {"theme": "dark"}),
                    ("clock", {"theme": "dark", "no_ticks": 1}),
                ):
                    with self.subTest(scene=scene, changes=changes), self.assertRaises(ValueError):
                        service.set_scene_options(scene, changes)
                    self.assertEqual(service.state(), before)
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()
