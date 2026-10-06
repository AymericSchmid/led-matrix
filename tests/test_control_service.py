"""The phone preview and matrix controls share persistent scene state."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from PIL import Image

from control_service import ControlService, SettingsStore


class ControlServiceTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
