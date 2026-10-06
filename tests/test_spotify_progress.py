"""Progress timing must survive pauses, seeks, missing data, and slow artwork."""

from dataclasses import replace
import colorsys
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageDraw, ImageStat

from main import MatrixEmulator
from spotify_client import PlayingTrack, parse_playback
from spotify_scene import (
    PROGRESS_PATH, SPOTIFY_STYLES, SpotifyView, SpotifyWorker, album_accent, dim_accent,
    album_palette, draw_vinyl_reflection, drift_palette, playback_progress, render_colour_drift_frame,
    render_spotify_frame, render_vinyl_frame, vinyl_needle_tip, vinyl_tonearm_points,
)


class ProgressTests(unittest.TestCase):
    def view(self, **changes):
        view = SpotifyView(
            Image.new("RGB", (64, 64), (80, 120, 160)), "Song",
            progress_ms=10_000, duration_ms=100_000, is_playing=True, sampled_at=100,
        )
        return replace(view, **changes)

    def test_position_parsing_handles_zero_null_and_invalid_data(self):
        payload = {"progress_ms": 0, "item": {"type": "track", "duration_ms": 180_000}}
        track = parse_playback(payload)
        self.assertEqual(track.progress_ms, 0)
        self.assertEqual(track.duration_ms, 180_000)
        for value in (None, -1, "unknown", True):
            payload["progress_ms"] = value
            self.assertIsNone(parse_playback(payload).progress_ms)

    def test_interpolation_pause_and_track_end(self):
        view = self.view()
        self.assertAlmostEqual(playback_progress(view, 105), .15)
        self.assertAlmostEqual(playback_progress(replace(view, is_playing=False), 105), .1)
        self.assertEqual(playback_progress(view, 1000), 1)
        self.assertAlmostEqual(playback_progress(view, 90), .1)
        self.assertIsNone(playback_progress(replace(view, progress_ms=None), 105))
        self.assertIsNone(playback_progress(replace(view, duration_ms=0), 105))

    def test_frame_keeps_whole_cover_clear_of_border(self):
        view = self.view(progress_ms=50_000, is_playing=False)
        view.frame.putpixel((0, 0), (255, 0, 0))
        original = view.frame.copy()
        result = render_spotify_frame(view)
        expected_art = view.frame.resize((56, 56), Image.Resampling.LANCZOS)
        self.assertIsNone(ImageChops.difference(result.crop((4, 4, 60, 60)), expected_art).getbbox())
        self.assertIsNone(ImageChops.difference(view.frame, original).getbbox())
        self.assertEqual(result.getpixel((2, 20)), (0, 0, 0))
        self.assertEqual(result.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(result.getpixel((1, 1)), view.accent_color)
        self.assertEqual(result.getpixel((1, 62)), dim_accent(view.accent_color))
        self.assertEqual((result.mode, result.size), ("RGB", (64, 64)))

    def test_empty_full_and_fractional_leading_pixel(self):
        self.assertEqual(len(PROGRESS_PATH), len(set(PROGRESS_PATH)))
        accent = self.view().accent_color
        for position, color in ((0, dim_accent(accent)), (100_000, accent)):
            frame = render_spotify_frame(self.view(progress_ms=position, is_playing=False))
            self.assertTrue(all(frame.getpixel(point) == color for point in PROGRESS_PATH))
        view = self.view(progress_ms=500, duration_ms=len(PROGRESS_PATH) * 1000, is_playing=False)
        first = render_spotify_frame(view).getpixel((1, 1))
        self.assertTrue(all(dim < value < full for dim, value, full in zip(dim_accent(accent), first, accent)))

    def test_accent_comes_from_colourful_area_and_lifts_dark_colours(self):
        cover = Image.new("RGB", (64, 64), (8, 8, 8))
        cover.paste((230, 100, 40), (16, 16, 48, 48))
        cover.putpixel((0, 0), (0, 255, 0))
        r, g, b = album_accent(cover)
        self.assertGreater(r, g)
        self.assertGreater(g, b)
        dark_blue = album_accent(Image.new("RGB", (64, 64), (10, 20, 60)))
        self.assertGreater(dark_blue[2], dark_blue[0])
        brightness = sum(value * weight for value, weight in zip(dark_blue, (0.2126, 0.7152, 0.0722)))
        self.assertGreaterEqual(brightness, 134)
        _, saturation, _ = colorsys.rgb_to_hsv(*(channel / 255 for channel in dark_blue))
        self.assertGreater(saturation, 0.20)
        cover = Image.new("RGB", (64, 64), (21, 63, 58))
        cover.paste((114, 211, 194), (16, 16, 48, 48))
        self.assertEqual(album_accent(cover), (114, 211, 194))

    def test_monochrome_cover_uses_neutral_accent(self):
        for value in (0, 100, 255):
            accent = album_accent(Image.new("RGB", (64, 64), (value, value, value)))
            self.assertEqual(accent[0], accent[1])
            self.assertEqual(accent[1], accent[2])
            self.assertGreater(accent[0], 150)

    def test_missing_timing_keeps_original_full_size_cover(self):
        view = self.view(progress_ms=None)
        frame = render_spotify_frame(view)
        self.assertIsNone(ImageChops.difference(view.frame, frame).getbbox())
        self.assertIsNot(view.frame, frame)

    def test_progress_can_be_disabled_without_resizing_or_mutating_cover(self):
        view = self.view()
        frame = render_spotify_frame(view, show_progress=False)
        self.assertIsNone(ImageChops.difference(view.frame, frame).getbbox())
        self.assertIsNot(view.frame, frame)
        self.assertEqual(frame.size, (64, 64))

    def test_vinyl_styles_have_a_record_and_distinct_cover_motion(self):
        view = self.view(progress_ms=None)
        for y in range(64):
            for x in range(64):
                view.frame.putpixel((x, y), ((x * 4) % 256, (y * 4) % 256, (x * 3 + y * 2) % 256))
        still_a = render_vinyl_frame(view, 0, spin_cover=False)
        still_b = render_vinyl_frame(view, 180, spin_cover=False)
        spin_a = render_vinyl_frame(view, 0, spin_cover=True)
        spin_b = render_vinyl_frame(view, 180, spin_cover=True)
        self.assertEqual((still_a.mode, still_a.size), ("RGB", (64, 64)))
        self.assertEqual(still_a.getpixel((31, 31)), (222, 215, 179))
        self.assertIsNotNone(ImageChops.difference(still_a, still_b).getbbox())
        self.assertIsNotNone(ImageChops.difference(spin_a, spin_b).getbbox())
        label_mask = Image.new("L", (20, 20), 0)
        ImageDraw.Draw(label_mask).ellipse((0, 0, 19, 19), fill=255)
        label_blank = Image.new("RGB", (20, 20))
        still_difference = ImageChops.difference(
            still_a.crop((22, 22, 42, 42)), still_b.crop((22, 22, 42, 42))
        )
        spin_difference = ImageChops.difference(
            spin_a.crop((22, 22, 42, 42)), spin_b.crop((22, 22, 42, 42))
        )
        self.assertIsNone(Image.composite(still_difference, label_blank, label_mask).getbbox())
        self.assertIsNotNone(Image.composite(spin_difference, label_blank, label_mask).getbbox())
        for style in SPOTIFY_STYLES:
            self.assertEqual(render_spotify_frame(view, style=style).size, (64, 64))
        with self.assertRaises(ValueError):
            render_spotify_frame(view, style="unknown")

    def test_vinyl_reflection_and_round_label_rotate_clockwise_together(self):
        cover = Image.new("RGB", (64, 64), (0, 0, 0))
        ImageDraw.Draw(cover).rectangle((25, 4, 38, 15), fill=(255, 0, 0))
        view = SpotifyView(cover, "Song", accent_color=(100, 100, 100), is_playing=False)
        initial = render_vinyl_frame(view, 0, spin_cover=True)
        quarter_turn = render_vinyl_frame(view, 90, spin_cover=True)
        still = render_vinyl_frame(view, 90, spin_cover=False)

        def red_center(frame):
            pixels = []
            for y in range(23, 41):
                for x in range(23, 41):
                    red, green, blue = frame.getpixel((x, y))
                    if red > max(green, blue) + 80:
                        pixels.append((x, y))
            self.assertTrue(pixels)
            return (sum(x for x, _ in pixels) / len(pixels),
                    sum(y for _, y in pixels) / len(pixels))

        start_x, start_y = red_center(initial)
        turned_x, turned_y = red_center(quarter_turn)
        still_x, still_y = red_center(still)
        self.assertLess(start_y, 31.5)
        self.assertGreater(turned_x, 31.5)
        self.assertAlmostEqual(still_x, start_x, delta=0.5)
        self.assertAlmostEqual(still_y, start_y, delta=0.5)
        self.assertLess(sum(initial.getpixel((22, 22))), 100)
        self.assertLess(sum(quarter_turn.getpixel((41, 22))), 100)

        reflection_0 = Image.new("RGB", (64, 64))
        reflection_90 = reflection_0.copy()
        draw_vinyl_reflection(reflection_0, 0)
        draw_vinyl_reflection(reflection_90, 90)
        clockwise_error = sum(ImageStat.Stat(ImageChops.difference(
            reflection_0.rotate(-90), reflection_90
        )).sum)
        opposite_error = sum(ImageStat.Stat(ImageChops.difference(
            reflection_0.rotate(90), reflection_90
        )).sum)
        self.assertLess(clockwise_error, opposite_error * 0.01)

    def test_vinyl_needle_moves_outward_with_playback_and_stops_when_paused(self):
        outer = self.view(progress_ms=0, duration_ms=100_000, is_playing=False)
        middle = self.view(progress_ms=50_000, duration_ms=100_000, is_playing=False)
        inner = self.view(progress_ms=100_000, duration_ms=100_000, is_playing=False)
        outer_tip = vinyl_needle_tip(outer, 200)
        middle_tip = vinyl_needle_tip(middle, 200)
        inner_tip = vinyl_needle_tip(inner, 200)
        self.assertLess(outer_tip[0], middle_tip[0])
        self.assertLess(middle_tip[0], inner_tip[0])
        self.assertNotEqual(vinyl_tonearm_points(outer)[1], vinyl_tonearm_points(inner)[1])

        playing = self.view(progress_ms=0, duration_ms=100_000, is_playing=True, sampled_at=100)
        self.assertGreater(vinyl_needle_tip(playing, 150)[0], vinyl_needle_tip(playing, 100)[0])

        paused_a = render_vinyl_frame(middle, 90, now=100)
        paused_b = render_vinyl_frame(middle, 90, now=200)
        self.assertIsNone(ImageChops.difference(paused_a, paused_b).getbbox())

    def test_colour_drift_moves_while_playing_and_freezes_when_paused(self):
        playing = self.view(progress_ms=20_000, duration_ms=100_000, is_playing=True, sampled_at=100)
        first = render_colour_drift_frame(playing, 0, now=100)
        second = render_colour_drift_frame(playing, 45, now=101)
        self.assertEqual((first.mode, first.size), ("RGB", (64, 64)))
        self.assertIsNotNone(ImageChops.difference(first, second).getbbox())
        self.assertGreater(len(drift_palette(playing.accent_color)), 2)

        paused = replace(playing, progress_ms=50_000, is_playing=False)
        paused_a = render_colour_drift_frame(paused, 0, now=100)
        paused_b = render_colour_drift_frame(paused, 180, now=200)
        self.assertIsNone(ImageChops.difference(paused_a, paused_b).getbbox())

    def test_colour_drift_can_hide_cover_and_use_brighter_fields(self):
        view = self.view(progress_ms=30_000, duration_ms=100_000, is_playing=False)
        for y in range(64):
            for x in range(64):
                view.frame.putpixel((x, y), ((x * 7) % 256, (y * 5) % 256, (x * 2 + y * 3) % 256))
        visible = render_colour_drift_frame(view, 45, now=100, show_cover=True)
        hidden = render_colour_drift_frame(view, 45, now=100, show_cover=False)
        self.assertIsNotNone(ImageChops.difference(visible, hidden).getbbox())
        self.assertIsNotNone(ImageChops.difference(hidden.crop((15, 15, 49, 49)), view.frame.resize((34, 34))).getbbox())
        soft = drift_palette(view.accent_color)
        bright = drift_palette(view.accent_color, bright=True)
        self.assertGreater(sum(bright[0]), sum(soft[0]))
        dark = render_colour_drift_frame(view, 45, now=100, show_cover=False, blackness=1.0)
        colourful = render_colour_drift_frame(view, 45, now=100, show_cover=False, blackness=0.0)
        dark_luminance = sum(ImageStat.Stat(dark).mean)
        colourful_luminance = sum(ImageStat.Stat(colourful).mean)
        self.assertGreater(colourful_luminance, dark_luminance)

    def test_colour_drift_palette_comes_from_the_cover(self):
        first_cover = Image.new("RGB", (64, 64), (220, 30, 45))
        first_cover.paste((25, 90, 220), (32, 0, 64, 32))
        first_cover.paste((35, 190, 100), (0, 32, 32, 64))
        second_cover = Image.new("RGB", (64, 64), (220, 185, 30))
        second_cover.paste((170, 35, 210), (32, 0, 64, 32))
        second_cover.paste((35, 190, 190), (0, 32, 32, 64))
        first_palette = album_palette(first_cover)
        second_palette = album_palette(second_cover)
        self.assertGreaterEqual(len(first_palette), 3)
        self.assertGreaterEqual(len(second_palette), 3)
        self.assertNotEqual(first_palette, second_palette)

        first = SpotifyView(first_cover, "First", accent_color=(120, 120, 120))
        second = SpotifyView(second_cover, "Second", accent_color=(120, 120, 120))
        first_frame = render_colour_drift_frame(first, 20, now=100, show_cover=False)
        second_frame = render_colour_drift_frame(second, 20, now=100, show_cover=False)
        self.assertIsNotNone(ImageChops.difference(first_frame, second_frame).getbbox())

    def test_ui_cycles_spotify_styles(self):
        app = MatrixEmulator.__new__(MatrixEmulator)
        app.scene = "spotify"
        app.spotify_style = "cover"
        app.progress_enabled = True
        app.show, app.caption, app.connect_button, app.track_button = Mock(), Mock(), Mock(), Mock()
        app.style_button = Mock()
        app._last_frame_key = None
        app._spotify_view = self.view(progress_ms=None)
        app.cycle_spotify_style()
        self.assertEqual(app.spotify_style, "vinyl-spin")
        app.cycle_spotify_style()
        self.assertEqual(app.spotify_style, "vinyl-still")
        app.cycle_spotify_style()
        self.assertEqual(app.spotify_style, "colour-drift")
        self.assertEqual(app.style_button.configure.call_args.kwargs["text"], "Style: SOFT COLOUR DRIFT")

    def test_colour_drift_cover_toggle_updates_the_frame(self):
        app = MatrixEmulator.__new__(MatrixEmulator)
        app.scene = "spotify"
        app.spotify_style = "colour-drift"
        app.progress_enabled = True
        app.drift_cover_enabled = True
        app.show, app.caption, app.connect_button, app.track_button = Mock(), Mock(), Mock(), Mock()
        app.progress_button, app.style_button, app.drift_cover_button = Mock(), Mock(), Mock()
        app._last_frame_key = None
        app._spotify_view = self.view(progress_ms=20_000, is_playing=False)
        app.toggle_drift_cover()
        self.assertFalse(app.drift_cover_enabled)
        self.assertEqual(app.drift_cover_button.configure.call_args.kwargs["text"], "Cover: OFF")
        hidden = app.show.call_args.args[0]
        app.toggle_drift_cover()
        visible = app.show.call_args.args[0]
        self.assertIsNotNone(ImageChops.difference(hidden, visible).getbbox())

    def test_colour_drift_blackness_control_updates_the_frame(self):
        app = MatrixEmulator.__new__(MatrixEmulator)
        app.scene = "spotify"
        app.spotify_style = "colour-drift"
        app.progress_enabled = True
        app.drift_cover_enabled = False
        app.drift_blackness = 35
        app.show, app.caption, app.connect_button, app.track_button = Mock(), Mock(), Mock(), Mock()
        app.progress_button, app.style_button = Mock(), Mock()
        app._last_frame_key = None
        app._spotify_view = self.view(progress_ms=20_000, is_playing=False)
        app.set_drift_blackness(80)
        self.assertEqual(app.drift_blackness, 80)
        dark = app.show.call_args.args[0]
        app.set_drift_blackness(0)
        colourful = app.show.call_args.args[0]
        self.assertIsNotNone(ImageChops.difference(dark, colourful).getbbox())

    def test_ui_progress_toggle_changes_only_spotify_frame(self):
        app = MatrixEmulator.__new__(MatrixEmulator)
        app.scene = "spotify"
        app.progress_enabled = True
        app.show, app.caption, app.connect_button, app.track_button = Mock(), Mock(), Mock(), Mock()
        app.progress_button = Mock()
        app._last_frame_key = None
        view = self.view()
        app.apply_spotify_view(view)
        bordered = app.show.call_args.args[0]
        app.toggle_progress()
        cover_only = app.show.call_args.args[0]
        self.assertFalse(app.progress_enabled)
        self.assertIsNone(ImageChops.difference(cover_only, view.frame).getbbox())
        self.assertIsNotNone(ImageChops.difference(bordered, cover_only).getbbox())
        self.assertEqual(app.progress_button.configure.call_args.kwargs["text"], "Progress: OFF")
        app.toggle_progress()
        self.assertTrue(app.progress_enabled)
        self.assertEqual(app.progress_button.configure.call_args.kwargs["text"], "Progress: ON")

    def test_cover_download_time_does_not_shift_progress_back(self):
        client = Mock()
        client.currently_playing.return_value = PlayingTrack(
            "Song", "Artist", "https://i.scdn.co/image/test", "", True,
            progress_ms=10_000, duration_ms=100_000, received_at=100,
        )
        client.cover.return_value = self.view().frame
        view = SpotifyWorker(client).poll_once()
        self.assertAlmostEqual(playback_progress(view, 103), .13)
        client.currently_playing.return_value = replace(client.currently_playing.return_value, image_url="")
        self.assertIsNone(playback_progress(SpotifyWorker(client).poll_once(), 103))

    def test_ui_updates_between_polls_and_accepts_identical_paused_sample(self):
        app = MatrixEmulator.__new__(MatrixEmulator)
        app.show, app.caption, app.connect_button, app.track_button = Mock(), Mock(), Mock(), Mock()
        app._last_frame_key = None
        with patch("spotify_scene.time.monotonic", return_value=100) as now:
            app.apply_spotify_view(self.view())
            now.return_value = 101
            app.refresh_spotify_display()
            self.assertEqual(app.show.call_count, 2)
            # The paused frame is identical now, but must replace the running timer.
            app.apply_spotify_view(self.view(progress_ms=11_000, sampled_at=101, is_playing=False))
            now.return_value = 110
            app.refresh_spotify_display()
            self.assertEqual(app.show.call_count, 2)
            app.apply_spotify_view(self.view(progress_ms=2_000, sampled_at=110))
            self.assertAlmostEqual(playback_progress(app._spotify_view, 110), .02)
            now.return_value = 111
            app.refresh_spotify_display()
            self.assertEqual(app.show.call_count, 4)


if __name__ == "__main__":
    unittest.main()
