"""Checks for the animated 64 × 64 eyes."""

import unittest

from PIL import ImageChops

from eyes_scene import EyesAnimator, EyesPose, render_eyes_frame
from main import render_leds


class EyesTests(unittest.TestCase):
    def test_gaze_changes_and_blinks_at_irregular_times(self):
        animator = EyesAnimator(now=0, seed=7)
        samples = [(step * 0.05, animator.pose(step * 0.05)) for step in range(1200)]
        gaze_positions = {(round(pose.gaze_x), round(pose.gaze_y)) for _, pose in samples}
        self.assertGreater(len(gaze_positions), 8)
        self.assertTrue(any(pose.left_open < 0.2 and pose.right_open < 0.2
                            for _, pose in samples))
        blink_starts = [time for (time, pose), (_, before) in zip(samples[1:], samples)
                        if pose.left_open < 0.8 and before.left_open >= 0.8]
        gaps = [round(b - a, 1) for a, b in zip(blink_starts, blink_starts[1:])]
        self.assertGreater(len(set(gaps)), 2)

    def test_pose_changes_visible_panel_pixels(self):
        idle = render_eyes_frame(EyesPose())
        looking = render_eyes_frame(EyesPose(gaze_x=4, gaze_y=-2))
        blink = render_eyes_frame(EyesPose(left_open=0, right_open=0))
        self.assertEqual((idle.mode, idle.size), ("RGB", (64, 64)))
        self.assertEqual(render_leds(idle).size, (576, 576))
        self.assertIsNotNone(ImageChops.difference(idle, looking).getbbox())
        self.assertIsNotNone(ImageChops.difference(idle, blink).getbbox())
        self.assertLess(sum(blink.getpixel((11, 32))), sum(idle.getpixel((11, 32))))

    def test_rates_change_timing_without_resetting_current_gaze(self):
        slow = EyesAnimator(now=0, seed=9, movement_rate=0.5, blink_rate=0.5)
        fast = EyesAnimator(now=0, seed=9, movement_rate=2, blink_rate=2)
        self.assertLess(fast.next_move, slow.next_move)
        self.assertLess(fast.next_blink, slow.next_blink)

        animator = EyesAnimator(now=0, seed=9)
        during_move = animator.next_move + 0.05
        before = animator.pose(during_move)
        animator.set_rates(2, 0.5, during_move)
        after = animator.pose(during_move)
        self.assertAlmostEqual(after.gaze_x, before.gaze_x)
        self.assertAlmostEqual(after.gaze_y, before.gaze_y)
        self.assertEqual(animator.movement_rate, 2)
        self.assertEqual(animator.blink_rate, 0.5)

    def test_frequency_can_be_rare_while_speed_stays_normal(self):
        rare = EyesAnimator(now=0, seed=4, movement_frequency=.05, movement_speed=1)
        normal = EyesAnimator(now=0, seed=4, movement_frequency=1, movement_speed=1)
        self.assertGreater(rare.next_move, normal.next_move * 10)
        self.assertEqual(rare.movement_speed, normal.movement_speed)

    def test_blinks_can_be_rare_without_changing_their_duration(self):
        rare = EyesAnimator(now=0, seed=4, blink_rate=.05)
        normal = EyesAnimator(now=0, seed=4, blink_rate=1)
        self.assertGreater(rare.next_blink, normal.next_blink * 10)
        self.assertEqual(rare.blink_duration, normal.blink_duration)


if __name__ == "__main__":
    unittest.main()
