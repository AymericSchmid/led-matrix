"""The luminous walking animation and its scene shortcut."""

import math
import unittest

from PIL import ImageChops

from main import MatrixEmulator, render_leds
from walker_scene import STEP_SECONDS, render_walker_frame


class WalkerTests(unittest.TestCase):
    def test_stride_changes_arms_and_legs_on_the_panel(self):
        first = render_walker_frame(math.pi / 2)
        opposite = render_walker_frame(3 * math.pi / 2)
        self.assertGreater(STEP_SECONDS, 0)
        self.assertEqual((first.mode, first.size), ("RGB", (64, 64)))
        self.assertEqual(render_leds(first).size, (576, 576))
        self.assertIsNotNone(ImageChops.difference(first, opposite).getbbox())
        self.assertIsNotNone(ImageChops.difference(
            first.crop((0, 39, 64, 64)), opposite.crop((0, 39, 64, 64))
        ).getbbox())
        self.assertGreater(sum(first.getpixel((25, 14))), sum(first.getpixel((42, 14))) * 3)
        scarf = first.getpixel((43, 24))
        self.assertGreater(scarf[0], scarf[2] + 80)
        self.assertGreater(sum(first.getpixel((20, 59))), 500)

    def test_d_cycles_eyes_walker_kintsugi_and_shadow(self):
        app = MatrixEmulator.__new__(MatrixEmulator)
        app.scene = "eyes"
        app._last_frame_key = b"old frame"
        app.cycle_on_d()
        self.assertEqual(app.scene, "walker")
        self.assertIsNone(app._last_frame_key)
        app.cycle_on_d()
        self.assertEqual(app.scene, "kintsugi")
        self.assertIsNotNone(app.kintsugi)
        app.cycle_on_d()
        self.assertEqual(app.scene, "shadow")
        self.assertIsNotNone(app.shadow)
        app.cycle_on_d()
        self.assertEqual(app.scene, "eyes")
        self.assertIsNotNone(app.eyes_animator)


if __name__ == "__main__":
    unittest.main()
