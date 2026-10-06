"""Checks for the impossible shadow's readable room and uncanny timing."""

import unittest

from PIL import ImageChops

from main import render_leds
from shadow_scene import CYCLE_SECONDS, ShadowAnimator, render_shadow_frame, shadow_pose


class ShadowTests(unittest.TestCase):
    def test_room_is_readable_and_shadow_changes_the_scene(self):
        empty = render_shadow_frame(0)
        approaching = render_shadow_frame(.76)
        retreating = render_shadow_frame(.91)
        self.assertEqual((empty.mode, empty.size), ("RGB", (64, 64)))
        self.assertEqual(render_leds(approaching).size, (576, 576))
        self.assertIsNotNone(ImageChops.difference(empty, approaching).getbbox())
        self.assertIsNotNone(ImageChops.difference(approaching, retreating).getbbox())
        self.assertGreater(sum(empty.getpixel((15, 15))), 100)

    def test_shadow_has_a_pause_a_reach_and_a_blink(self):
        early = shadow_pose(.20)
        noticed = shadow_pose(.58)
        reached = shadow_pose(.76)
        gone = shadow_pose(.93)
        self.assertGreater(early.rise, 0)
        self.assertGreater(noticed.eyes, 0)
        self.assertGreater(reached.approach, .5)
        self.assertEqual(gone.retreat, 1)
        self.assertGreater(noticed.sway, -2)

    def test_speed_preserves_the_current_moment(self):
        animator = ShadowAnimator(now=10, seed=3)
        animator.frame(10 + CYCLE_SECONDS * .5)
        phase = animator.phase
        animator.frame(animator.last_time, speed=200)
        self.assertEqual(animator.phase, phase)
        animator.frame(animator.last_time + 1, speed=200)
        self.assertAlmostEqual(animator.phase, phase + 2 / CYCLE_SECONDS)


if __name__ == "__main__":
    unittest.main()
