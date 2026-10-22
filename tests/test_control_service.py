"""The phone preview and matrix controls share persistent scene state."""

from io import BytesIO
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch, Mock
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
import json
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from control_service import ControlService, SettingsStore
from spotify_client import SpotifyError
from weather_scene import weather_kind


class ControlServiceTests(unittest.TestCase):
    def service_fixture(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        service = ControlService(store=SettingsStore(root / 'state.db'), spotify_data_dir=root / 'spotify')
        self.addCleanup(service.stop)
        return service

    def test_power_and_relative_brightness(self):
        service = self.service_fixture()
        service.set_brightness(40)
        off = service.set_brightness(0)
        self.assertEqual((off['power'], off['brightness'], off['applied_brightness']), (False, 40, 0))
        self.assertEqual(service.set_brightness(0)['revision'], off['revision'])
        self.assertEqual(service.adjust_brightness(10)['brightness'], 50)
        self.assertFalse(service.state()['power'])
        self.assertEqual(service.adjust_brightness(500)['brightness'], 100)
        zero = service.adjust_brightness(-500)
        self.assertEqual((zero['applied_brightness'], zero['brightness']), (0, 100))
        self.assertFalse(zero['power'])
        self.assertTrue(service.set_power(True)['power'])
        self.assertNotIn('applied_brightness', service.state())
        service.set_power(False)
        service.set_scene_options('clock', {'theme': 'dark'})
        self.assertFalse(service.state()['power'])

    def test_strict_command_types(self):
        service = self.service_fixture()
        before = service.state()
        with patch.object(service.store, 'save', wraps=service.store.save) as save:
            for method in (service.set_brightness, service.adjust_brightness):
                for value in (True, False, 1.5, '40', None):
                    with self.subTest(method=method.__name__, value=value), self.assertRaises(ValueError):
                        method(value)
            for value in (-1, 101):
                with self.assertRaises(ValueError):
                    service.set_brightness(value)
            for value in (0, 1, 'on', None, 1.0):
                with self.assertRaises(ValueError):
                    service.set_power(value)
            self.assertEqual(service.state(), before)
            save.assert_not_called()

    def test_noop_does_not_save(self):
        service = self.service_fixture()
        before = service.state()
        with patch.object(service.store, 'save', wraps=service.store.save) as save:
            service.set_power(True)
            service.set_brightness(80)
            service.adjust_brightness(0)
            service.select_scene('clock')
            service.set_scene_options('clock', {'theme': 'light'})
            service.set_gpio_slowdown(2)
            save.assert_not_called()
        self.assertEqual(service.state(), before)

    def test_legacy_and_power_round_trip(self):
        service = self.service_fixture()
        import sqlite3
        with closing(sqlite3.connect(service.store.path)) as db:
            db.execute("UPDATE settings SET value=?", (json.dumps({'eyes_movement_rate': 45, 'scene': 'moon'}),))
            db.commit()
        legacy = service.store.load()
        self.assertTrue(legacy.power)
        self.assertEqual((legacy.eyes_movement_frequency, legacy.eyes_movement_speed), (45, 45))
        service.select_scene('moon')
        service.set_brightness(40)
        service.set_power(False)
        saved = service.store.load()
        self.assertEqual((saved.power, saved.brightness, saved.scene), (False, 40, 'moon'))

    def test_relative_changes_are_atomic(self):
        service = self.service_fixture()
        service.set_brightness(40)
        barrier = Barrier(2)
        def adjust():
            barrier.wait(timeout=2)
            return service.adjust_brightness(10)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: adjust(), range(2)))
        self.assertEqual(service.state()['brightness'], 60)
        self.assertEqual(len({r['revision'] for r in results}), 2)

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
                service.set_scene_options("eyes", {
                    "eyes_movement_frequency": 175, "eyes_movement_speed": 100, "eyes_blink_rate": 5,
                })
                service.select_scene("eyes")
                animator = service.source.eyes
                service.set_scene_options("eyes", {"eyes_movement_frequency": 5, "eyes_movement_speed": 100})
                service.source.frame()
                self.assertIs(service.source.eyes, animator)
                self.assertEqual(animator.movement_frequency, .05)
                self.assertEqual(animator.movement_speed, 1)
                self.assertEqual(animator.blink_rate, 0.05)
                service.select_scene("kintsugi")
                ceramic = service.source.kintsugi
                service.set_scene_options("kintsugi", {
                    "kintsugi_theme": "ivory", "kintsugi_speed": 175, "kintsugi_density": 80,
                })
                frame, caption = service.source.frame()
                self.assertIs(service.source.kintsugi, ceramic)
                self.assertEqual(frame.size, (64, 64))
                self.assertTrue(caption.startswith("Kintsugi"))
                self.assertEqual(ceramic.speed, 175)
                service.select_scene("shadow")
                apparition = service.source.shadow
                service.set_scene_options("shadow", {"shadow_speed": 150, "shadow_frequency": 5, "shadow_intensity": 80})
                frame, caption = service.source.frame()
                self.assertIs(service.source.shadow, apparition)
                self.assertEqual(frame.size, (64, 64))
                self.assertTrue(caption.startswith("Impossible shadow"))
            finally:
                service.stop()

            restarted = ControlService(store=store)
            try:
                self.assertEqual(restarted.state()["theme"], "dark")
                self.assertEqual(restarted.state()["spotify_style"], "colour-drift")
                self.assertEqual(restarted.state()["weather_demo"], "rain")
                self.assertEqual(restarted.state()["eyes_movement_frequency"], 5)
                self.assertEqual(restarted.state()["eyes_movement_speed"], 100)
                self.assertEqual(restarted.state()["eyes_blink_rate"], 5)
                self.assertEqual(restarted.state()["scene"], "shadow")
                self.assertEqual(restarted.state()["kintsugi_theme"], "ivory")
                self.assertEqual(restarted.state()["kintsugi_speed"], 175)
                self.assertEqual(restarted.state()["kintsugi_density"], 80)
                self.assertEqual(restarted.state()["scene"], "shadow")
                self.assertEqual(restarted.state()["shadow_speed"], 150)
                self.assertEqual(restarted.state()["shadow_frequency"], 5)
                self.assertEqual(restarted.state()["shadow_intensity"], 80)
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
                    ("eyes", {"eyes_movement_frequency": 4}),
                    ("eyes", {"eyes_movement_speed": 24}),
                    ("eyes", {"eyes_blink_rate": 4}),
                    ("eyes", {"eyes_blink_rate": 301}),
                    ("kintsugi", {"kintsugi_theme": "blue"}),
                    ("kintsugi", {"kintsugi_density": 10}),
                    ("kintsugi", {"kintsugi_speed": 0}),
                    ("shadow", {"shadow_speed": 201}),
                    ("shadow", {"shadow_frequency": 4}),
                    ("shadow", {"shadow_intensity": 24}),
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
