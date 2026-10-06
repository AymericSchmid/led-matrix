"""Phase timing and LED-frame checks for the Moon scene."""

from datetime import datetime, timedelta, timezone
import unittest

from main import render_leds
from moon_scene import (
    illumination, moon_phase, nighttime_reference, phase_name, render_moon_frame,
)


class MoonTests(unittest.TestCase):
    def test_phase_matches_published_2026_primary_phases(self):
        # Primary phase times from the US Naval Observatory, in UTC.
        expected = (
            (datetime(2026, 10, 10, 15, 50, tzinfo=timezone.utc), "New Moon"),
            (datetime(2026, 10, 18, 16, 12, tzinfo=timezone.utc), "First Quarter"),
            (datetime(2026, 10, 26, 4, 12, tzinfo=timezone.utc), "Full Moon"),
            (datetime(2026, 11, 1, 20, 28, tzinfo=timezone.utc), "Last Quarter"),
        )
        for instant, name in expected:
            self.assertEqual(phase_name(moon_phase(instant)), name)
        self.assertLess(illumination(moon_phase(expected[0][0])), 0.01)
        self.assertGreater(illumination(moon_phase(expected[2][0])), 0.98)

    def test_current_or_next_night_uses_local_clock(self):
        local_zone = timezone(timedelta(hours=2))
        at_2am = datetime(2026, 10, 6, 2, 15, tzinfo=local_zone)
        at_2pm = at_2am.replace(hour=14)
        self.assertEqual(nighttime_reference(at_2am).astimezone(local_zone).hour, 0)
        self.assertEqual(nighttime_reference(at_2pm).astimezone(local_zone).hour, 22)

    def test_full_size_disk_and_waxing_direction(self):
        full = render_moon_frame(0.5)
        waxing = render_moon_frame(0.25)
        waning = render_moon_frame(0.75)
        self.assertEqual((full.mode, full.size), ("RGB", (64, 64)))
        self.assertEqual(render_leds(full).size, (576, 576))
        self.assertGreater(sum(full.getpixel((32, 4))), 200)
        self.assertGreater(sum(full.getpixel((32, 59))), 200)
        self.assertGreater(sum(waxing.getpixel((46, 32))),
                           sum(waxing.getpixel((18, 32))) * 2)
        self.assertGreater(sum(waning.getpixel((18, 32))),
                           sum(waning.getpixel((46, 32))) * 2)
        self.assertLess(sum(render_moon_frame(0).getpixel((32, 32))),
                        sum(full.getpixel((32, 32))) / 3)


if __name__ == "__main__":
    unittest.main()
