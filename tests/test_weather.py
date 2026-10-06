"""Offline checks for the live weather lookup and 64 × 64 weather scenes."""

import unittest
from unittest.mock import Mock, patch

from PIL import ImageChops

from main import MatrixEmulator, render_leds
from weather_scene import (
    CURRENT_VARIABLES, DEMO_WEATHERS, FORECAST_URL, GEOCODING_URL,
    WeatherClient, WeatherError, demo_weather, parse_weather,
    rain_drop_position, render_weather_frame, sun_position, weather_kind,
)


class WeatherTests(unittest.TestCase):
    def test_weather_codes_choose_distinct_scenes(self):
        for code, expected in ((0, "clear"), (3, "cloud"), (45, "fog"),
                               (63, "rain"), (73, "snow"), (95, "storm")):
            self.assertEqual(weather_kind(code), expected)

    def test_current_response_parsing_and_invalid_data(self):
        payload = {"utc_offset_seconds": 7200, "daily": {
            "sunrise": ["2026-10-06T07:35"], "sunset": ["2026-10-06T19:02"],
        }, "current": {
            "temperature_2m": 11.7, "weather_code": 63,
            "cloud_cover": 120, "precipitation": 1.2,
            "wind_speed_10m": 14.3, "time": "2026-10-06T14:15",
        }}
        weather = parse_weather(payload, "Fribourg")
        self.assertEqual((weather.city, weather.temperature_c, weather.code), ("Fribourg", 11.7, 63))
        self.assertEqual(weather.cloud_cover, 100)
        self.assertEqual(weather.observed_at, "2026-10-06T14:15")
        self.assertAlmostEqual(weather.sunrise_hour, 7 + 35 / 60)
        self.assertAlmostEqual(weather.sunset_hour, 19 + 2 / 60)
        self.assertEqual(weather.utc_offset_seconds, 7200)
        with self.assertRaises(WeatherError):
            parse_weather({"current": {"temperature_2m": 11}}, "Fribourg")

    def test_client_resolves_city_then_reuses_location(self):
        geocoding = {"results": [{"name": "Fribourg", "latitude": 46.8065, "longitude": 7.1619}]}
        forecast = {"utc_offset_seconds": 7200, "daily": {
            "sunrise": ["2026-10-06T07:35"], "sunset": ["2026-10-06T19:02"],
        }, "current": {
            "temperature_2m": 12, "weather_code": 2,
            "cloud_cover": 40, "precipitation": 0, "wind_speed_10m": 5,
        }}
        with patch("weather_scene.request_weather_json", side_effect=[geocoding, forecast, forecast]) as request:
            client = WeatherClient()
            self.assertEqual(client.current("Fribourg, Switzerland").city, "Fribourg")
            self.assertEqual(client.current("Fribourg, Switzerland").code, 2)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args_list[0].args[0], GEOCODING_URL)
        self.assertEqual(request.call_args_list[1].args[0], FORECAST_URL)
        self.assertEqual(request.call_args_list[1].args[1]["current"], CURRENT_VARIABLES)
        self.assertEqual(request.call_args_list[1].args[1]["daily"], "sunrise,sunset")

    def test_weather_demo_frames_are_distinct_and_panel_sized(self):
        frames = [render_weather_frame(demo_weather(kind), phase=5, local_hour=13)
                  for kind in DEMO_WEATHERS]
        self.assertTrue(all((frame.mode, frame.size) == ("RGB", (64, 64)) for frame in frames))
        self.assertEqual(render_leds(frames[0]).size, (576, 576))
        for first, second in zip(frames, frames[1:]):
            self.assertIsNotNone(ImageChops.difference(first, second).getbbox())
        self.assertNotIn("night", DEMO_WEATHERS)
        for kind in DEMO_WEATHERS:
            night = render_weather_frame(demo_weather(kind), phase=5, local_hour=23)
            day = render_weather_frame(demo_weather(kind), phase=5, local_hour=13)
            self.assertIsNotNone(ImageChops.difference(day, night).getbbox(), kind)

    def test_rain_follows_streak_and_temperature_remains_visible(self):
        rain = demo_weather("rain")
        x1, y1 = rain_drop_position(1, 0)
        x2, y2 = rain_drop_position(1, 0.5)
        self.assertGreater(y2, y1)
        self.assertAlmostEqual((x2 - x1) / (y2 - y1), -2 / 5)
        first = render_weather_frame(rain, phase=5)
        later = render_weather_frame(rain, phase=6)
        self.assertIsNotNone(ImageChops.difference(first, later).getbbox())
        for phase in (1, 5, 19, 25):
            with_temp = render_weather_frame(rain, phase=phase)
            without_temp = render_weather_frame(rain, phase=phase, show_temperature=False)
            self.assertIsNotNone(ImageChops.difference(with_temp, without_temp).getbbox())
            self.assertIsNone(ImageChops.difference(
                with_temp.crop((0, 0, 64, 50)), without_temp.crop((0, 0, 64, 50))
            ).getbbox())

    def test_sun_moves_with_day_and_daylight_is_brighter_than_night(self):
        clear = demo_weather("clear")
        morning = sun_position(clear, 8)
        noon = sun_position(clear, 12)
        evening = sun_position(clear, 17)
        self.assertLess(morning[0], noon[0])
        self.assertLess(noon[0], evening[0])
        self.assertLess(noon[1], morning[1])
        self.assertLess(noon[1], evening[1])
        day = render_weather_frame(clear, local_hour=12, show_temperature=False)
        night = render_weather_frame(clear, local_hour=23, show_temperature=False)
        self.assertGreater(sum(day.getpixel((3, 3))), sum(night.getpixel((3, 3))) * 3)
        dawn = render_weather_frame(clear, local_hour=6.5, show_temperature=False)
        self.assertNotEqual(day.getpixel((3, 30)), dawn.getpixel((3, 30)))

    def test_demo_cycles_through_weather_scenes_in_emulator(self):
        app = MatrixEmulator.__new__(MatrixEmulator)
        app.scene = "weather"
        app.weather_demo = "clear"
        app.weather_demo_hour = 13
        app.weather_city = "Fribourg, Switzerland"
        app._weather_conditions = demo_weather("clear")
        app._weather_error = ""
        app._last_frame_key = None
        app.show, app.caption = Mock(), Mock()
        app.refresh_weather_display()
        first = app.show.call_args.args[0]
        app.cycle_on_d()
        self.assertEqual(app.weather_demo, "cloud")
        second = app.show.call_args.args[0]
        self.assertIsNotNone(ImageChops.difference(first, second).getbbox())
        self.assertIn("Cloudy", app.caption.configure.call_args.kwargs["text"])
        app.adjust_weather_demo_time(10)
        self.assertEqual(app.weather_demo_hour, 23)
        self.assertEqual(app.weather_demo, "cloud")
        self.assertIn("23:00", app.caption.configure.call_args.kwargs["text"])
        night = app.show.call_args.args[0]
        self.assertIsNotNone(ImageChops.difference(second, night).getbbox())
        app.adjust_weather_demo_time(1)
        self.assertEqual(app.weather_demo_hour, 0)
        app.adjust_weather_demo_time(-1)
        self.assertEqual(app.weather_demo_hour, 23)


if __name__ == "__main__":
    unittest.main()
