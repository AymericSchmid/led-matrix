"""Fracture, gold propagation, renewal, and continuous live timing."""

import math
import unittest

from kintsugi_scene import CYCLE_SECONDS, KintsugiAnimator, _pattern, render_kintsugi_frame


class KintsugiTests(unittest.TestCase):
    def test_gold_fills_the_cracks_and_returns_to_intact_ceramic(self):
        for theme in ("indigo", "ivory"):
            with self.subTest(theme=theme):
                intact = render_kintsugi_frame(0, theme=theme)
                broken = render_kintsugi_frame(.10, theme=theme)
                repaired = render_kintsugi_frame(.68, theme=theme)
                renewed = render_kintsugi_frame(1, theme=theme)
                self.assertEqual((repaired.mode, repaired.size), ("RGB", (64, 64)))
                self.assertNotEqual(intact.tobytes(), broken.tobytes())
                self.assertNotEqual(broken.tobytes(), repaired.tobytes())
                self.assertEqual(intact.tobytes(), renewed.tobytes())
                pixels = repaired.tobytes()
                gold_pixels = sum(r > b + 90 and g > b + 45 for r, g, b in zip(pixels[::3], pixels[1::3], pixels[2::3]))
                self.assertGreater(gold_pixels, 60)

    def test_patterns_are_reproducible_connected_and_vary_between_tiles(self):
        for seed in (0, 7, 81, 1079):
            for density in (20, 50, 100):
                seams = _pattern(seed, density).seams
                self.assertTrue(all(math.isfinite(s.arrival) and 0 <= s.arrival <= 1 for s in seams))
        first = render_kintsugi_frame(.65, seed=7)
        self.assertEqual(first.tobytes(), render_kintsugi_frame(.65, seed=7).tobytes())
        self.assertNotEqual(first.tobytes(), render_kintsugi_frame(.65, seed=8).tobytes())
        self.assertGreater(len(_pattern(7, 100).cells), len(_pattern(7, 20).cells))

    def test_pace_preserves_phase_and_next_cycle_has_a_new_pattern(self):
        animator = KintsugiAnimator(now=100, seed=7)
        first = animator.frame(100 + CYCLE_SECONDS * .65)
        before = animator.phase
        animator.frame(animator.last_time, speed=200)
        self.assertEqual(animator.phase, before)
        animator.frame(animator.last_time + 1, speed=200)
        self.assertAlmostEqual(animator.phase, before + 2 / CYCLE_SECONDS)
        animator.frame(animator.last_time, speed=100)
        second = animator.frame(animator.last_time + CYCLE_SECONDS - 2)
        self.assertAlmostEqual(animator.phase, before)
        self.assertNotEqual(first.tobytes(), second.tobytes())
        animator.frame(animator.last_time, density=80)
        self.assertEqual(animator.phase, 0)


if __name__ == "__main__":
    unittest.main()
