"""Weather glass: live conditions and small, animated 64 × 64 skies."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from queue import Empty, Full, Queue
from threading import Event, Thread
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFilter, ImageFont


DEFAULT_CITY = "Fribourg, Switzerland"
DEMO_WEATHERS = ("clear", "cloud", "rain", "snow", "fog", "storm")
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_VARIABLES = "temperature_2m,weather_code,cloud_cover,precipitation,wind_speed_10m"


class WeatherError(Exception):
    """A recoverable weather lookup or response error."""


@dataclass(frozen=True)
class WeatherConditions:
    city: str
    temperature_c: float
    code: int
    cloud_cover: int = 0
    precipitation_mm: float = 0.0
    wind_kmh: float = 0.0
    observed_at: str = ""
    sunrise_hour: float = 6.0
    sunset_hour: float = 18.0
    utc_offset_seconds: int = 0


def weather_kind(code: int) -> str:
    if code in (95, 96, 97, 99):
        return "storm"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (2, 3):
        return "cloud"
    return "clear"


WEATHER_LABELS = {
    "clear": "Clear sky", "cloud": "Cloudy", "rain": "Rain",
    "snow": "Snow", "fog": "Fog", "storm": "Thunderstorm",
}


def demo_weather(kind: str, city: str = "Fribourg") -> WeatherConditions:
    if kind not in DEMO_WEATHERS:
        raise ValueError(f"Unknown weather demo: {kind}")
    samples = {
        "clear": (0, 22, 5, 0),
        "cloud": (3, 17, 84, 0),
        "rain": (63, 12, 96, 2.4),
        "snow": (73, -2, 91, 1.3),
        "fog": (45, 6, 100, 0),
        "storm": (95, 15, 100, 5.6),
    }
    code, temperature, cloud_cover, precipitation = samples[kind]
    return WeatherConditions(city, temperature, code, cloud_cover, precipitation)


def _hour_from_iso(value: str) -> float:
    instant = datetime.fromisoformat(value)
    return instant.hour + instant.minute / 60 + instant.second / 3600


def parse_weather(payload: dict, city: str) -> WeatherConditions:
    try:
        current = payload["current"]
        temperature = float(current["temperature_2m"])
        code = int(current["weather_code"])
        cloud_cover = int(current.get("cloud_cover", 0))
        precipitation = float(current.get("precipitation", 0))
        wind = float(current.get("wind_speed_10m", 0))
        observed_at = str(current.get("time", ""))
        daily = payload["daily"]
        sunrise_hour = _hour_from_iso(daily["sunrise"][0])
        sunset_hour = _hour_from_iso(daily["sunset"][0])
        utc_offset_seconds = int(payload["utc_offset_seconds"])
        if not math.isfinite(temperature):
            raise ValueError("Invalid weather values")
        if not math.isfinite(precipitation) or not math.isfinite(wind):
            raise ValueError("Invalid weather values")
        if not 0 <= sunrise_hour < sunset_hour < 24:
            raise ValueError("Invalid sun times")
    except (KeyError, TypeError, ValueError, OverflowError, IndexError) as error:
        raise WeatherError("Weather data is incomplete. Trying again shortly.") from error
    return WeatherConditions(
        city, temperature, code, max(0, min(100, cloud_cover)),
        max(0.0, precipitation), max(0.0, wind), observed_at,
        sunrise_hour, sunset_hour, utc_offset_seconds,
    )


def request_weather_json(url: str, parameters: dict) -> dict:
    request = Request(f"{url}?{urlencode(parameters)}", headers={"User-Agent": "LEDMatrixWeatherGlass/1.0"})
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except (OSError, ValueError) as error:
        raise WeatherError("Could not reach the weather service. Retrying shortly.") from error
    if not isinstance(payload, dict) or payload.get("error"):
        raise WeatherError("Weather service returned an error. Retrying shortly.")
    return payload


class WeatherClient:
    def __init__(self):
        self.locations: dict[str, tuple[str, float, float]] = {}

    def resolve(self, city: str) -> tuple[str, float, float]:
        city = city.strip()
        if len(city) < 2:
            raise WeatherError("Enter a city name with at least two characters.")
        if city in self.locations:
            return self.locations[city]
        payload = request_weather_json(GEOCODING_URL, {"name": city, "count": 1, "language": "en", "format": "json"})
        results = payload.get("results") or []
        if not results:
            raise WeatherError(f"No matching city found for {city}.")
        try:
            first = results[0]
            location = (str(first["name"]), float(first["latitude"]), float(first["longitude"]))
        except (KeyError, TypeError, ValueError, IndexError) as error:
            raise WeatherError("Could not read the weather location.") from error
        self.locations[city] = location
        return location

    def current(self, city: str) -> WeatherConditions:
        name, latitude, longitude = self.resolve(city)
        payload = request_weather_json(FORECAST_URL, {
            "latitude": latitude, "longitude": longitude,
            "current": CURRENT_VARIABLES, "daily": "sunrise,sunset",
            "timezone": "auto", "forecast_days": 1,
        })
        return parse_weather(payload, name)


@dataclass(frozen=True)
class WeatherUpdate:
    conditions: WeatherConditions | None = None
    error: str = ""


class WeatherWorker:
    def __init__(self, city: str, client: WeatherClient | None = None):
        self.city = city
        self.client = client or WeatherClient()
        self.updates: Queue[WeatherUpdate] = Queue(maxsize=1)
        self.stop = Event()
        self.wake = Event()
        self.thread = Thread(target=self._run, daemon=True, name="weather-poll")

    def start(self) -> None:
        self.thread.start()

    def set_city(self, city: str) -> None:
        self.city = city
        self.latest()  # Discard an update for the previous city, if one is waiting.
        self.wake.set()

    def close(self) -> None:
        self.stop.set()
        self.wake.set()

    def latest(self) -> WeatherUpdate | None:
        try:
            return self.updates.get_nowait()
        except Empty:
            return None

    def _publish(self, update: WeatherUpdate) -> None:
        try:
            self.updates.put_nowait(update)
        except Full:
            self.latest()
            self.updates.put_nowait(update)

    def _run(self) -> None:
        while not self.stop.is_set():
            self.wake.clear()
            city = self.city
            try:
                update = WeatherUpdate(conditions=self.client.current(city))
                delay = 15 * 60
            except WeatherError as error:
                update = WeatherUpdate(error=str(error))
                delay = 60
            if city == self.city and not self.stop.is_set():
                self._publish(update)
            self.wake.wait(delay)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return tuple(round(start + (end - start) * ratio) for start, end in zip(a, b))


def _sky_colors(
    kind: str, is_day: bool, daylight_progress: float,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if not is_day:
        return {
            "clear": ((12, 24, 61), (35, 69, 110)),
            "cloud": ((13, 27, 62), (43, 70, 111)),
            "rain": ((12, 24, 58), (38, 66, 102)),
            "snow": ((17, 33, 71), (51, 78, 120)),
            "fog": ((20, 35, 69), (55, 80, 112)),
            "storm": ((10, 20, 52), (35, 58, 97)),
        }[kind]

    midday = {
        "clear": ((24, 112, 225), (145, 219, 238)),
        "cloud": ((42, 112, 183), (151, 199, 219)),
        "rain": ((47, 91, 151), (123, 173, 198)),
        "snow": ((61, 136, 196), (191, 225, 234)),
        "fog": ((103, 151, 175), (195, 219, 218)),
        "storm": ((38, 73, 133), (105, 154, 187)),
    }[kind]
    dawn = ((52, 82, 155), (250, 164, 111))
    dusk = ((43, 65, 132), (248, 123, 102))
    if daylight_progress < 0.25:
        return tuple(_mix(a, b, daylight_progress * 4) for a, b in zip(dawn, midday))
    if daylight_progress > 0.75:
        return tuple(_mix(a, b, (daylight_progress - 0.75) * 4) for a, b in zip(midday, dusk))
    return midday


def _local_hour(weather: WeatherConditions) -> float:
    local_now = datetime.now(timezone.utc) + timedelta(seconds=weather.utc_offset_seconds)
    return local_now.hour + local_now.minute / 60 + local_now.second / 3600


def sun_position(weather: WeatherConditions, local_hour: float) -> tuple[int, int]:
    """Place the sun along a low sunrise, high noon, low sunset arc."""
    progress = max(0.0, min(1.0, (local_hour - weather.sunrise_hour) /
                             (weather.sunset_hour - weather.sunrise_hour)))
    return round(5 + progress * 54), round(37 - 24 * math.sin(math.pi * progress))


def rain_drop_position(index: int, phase: float) -> tuple[float, float]:
    """Move a slanted streak down and left by the same ratio as its drawn line."""
    distance = (index * 19 + phase * (12 + index % 5)) % 68
    x = (index * 37 + index // 3 * 11 - distance * 0.4) % 70 - 3
    return x, distance - 4


STAR_POSITIONS = (
    (5, 8), (13, 3), (22, 12), (32, 5), (52, 10), (60, 4),
    (8, 22), (19, 27), (38, 17), (56, 29), (27, 35), (47, 36),
)


def _draw_cloud(draw: ImageDraw.ImageDraw, x: float, y: float, width: int, color: tuple[int, ...]) -> None:
    x, y = round(x), round(y)
    draw.ellipse((x, y + 4, x + width, y + 12), fill=color)
    draw.ellipse((x + 5, y, x + width - 10, y + 12), fill=color)
    draw.ellipse((x + width // 3, y - 3, x + width - 5, y + 11), fill=color)


def render_weather_frame(
    weather: WeatherConditions, phase: float = 0.0, *, show_temperature: bool = True,
    local_hour: float | None = None,
) -> Image.Image:
    """Render one RGB frame; phase is elapsed animation time in seconds."""
    kind = weather_kind(weather.code)
    hour = _local_hour(weather) if local_hour is None else local_hour
    is_day = weather.sunrise_hour <= hour < weather.sunset_hour
    daylight_progress = ((hour - weather.sunrise_hour) /
                         (weather.sunset_hour - weather.sunrise_hour)) if is_day else 0.0
    top, bottom = _sky_colors(kind, is_day, daylight_progress)
    frame = Image.new("RGB", (64, 64))
    draw = ImageDraw.Draw(frame)
    for y in range(64):
        draw.line((0, y, 63, y), fill=_mix(top, bottom, min(1.0, y / 58)))

    if not is_day and kind == "clear" and weather.cloud_cover < 25:
        for index, (x, y) in enumerate(STAR_POSITIONS):
            twinkle = 0.68 + 0.32 * math.sin(phase * 1.2 + index)
            draw.point((x, y), fill=tuple(round(channel * twinkle) for channel in (121, 160, 183)))

    if kind in ("clear", "cloud", "snow"):
        light = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        light_draw = ImageDraw.Draw(light)
        center = sun_position(weather, hour) if is_day else (44, 18)
        glow = (255, 207, 127) if is_day else (151, 191, 214)
        for radius, alpha in ((16, 15), (11, 29), (7, 42)):
            light_draw.ellipse((center[0] - radius, center[1] - radius,
                                center[0] + radius, center[1] + radius), fill=(*glow, alpha))
        frame = Image.alpha_composite(frame.convert("RGBA"), light.filter(ImageFilter.GaussianBlur(3)))
        draw = ImageDraw.Draw(frame)
        if is_day:
            if kind == "clear":
                draw.ellipse((center[0] - 5, center[1] - 5, center[0] + 5, center[1] + 5),
                             fill=(255, 231, 154), outline=(255, 249, 202))
            else:
                draw.ellipse((center[0] - 4, center[1] - 4, center[0] + 4, center[1] + 4),
                             fill=(247, 229, 181))
        else:
            draw.ellipse((39, 13, 49, 23), fill=(217, 226, 221))
            draw.ellipse((43, 10, 53, 22), fill=(*top, 255))
    else:
        frame = frame.convert("RGBA")

    if kind != "clear" or weather.cloud_cover > 20:
        cloud_layer = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        clouds = ImageDraw.Draw(cloud_layer)
        count = 1 if kind in ("clear", "fog") else 3
        if kind == "cloud":
            count = 1 + min(2, weather.cloud_cover // 40)
        color = (219, 233, 231, 196) if is_day else (71, 98, 123, 183)
        if kind in ("rain", "storm"):
            color = (57, 83, 109, 229) if is_day else (27, 48, 72, 245)
        for index in range(count):
            speed = 0.7 + index * 0.2 + min(1.0, weather.wind_kmh / 30)
            x = ((index * 29 - 16 + phase * speed) % 92) - 20
            y = 22 + index * 11
            _draw_cloud(clouds, x, y, 31 + index * 4, color)
        frame = Image.alpha_composite(frame, cloud_layer)

    draw = ImageDraw.Draw(frame)
    mountain = (57, 111, 136, 255) if is_day else (17, 34, 55, 255)
    foreground = (25, 72, 93, 255) if is_day else (10, 26, 44, 255)
    draw.polygon(((0, 49), (12, 43), (23, 48), (34, 42), (48, 49), (63, 44), (63, 63), (0, 63)), fill=mountain)
    draw.polygon(((0, 56), (14, 52), (29, 57), (45, 51), (63, 55), (63, 63), (0, 63)), fill=foreground)

    if kind in ("rain", "storm"):
        rain = ImageDraw.Draw(frame)
        count = min(52, (35 if kind == "storm" else 27) + round(weather.precipitation_mm * 3))
        for index in range(count):
            x, y = rain_drop_position(index, phase)
            color = (164, 209, 221, 230) if index % 3 else (100, 168, 193, 190)
            rain.line((x, y, x - 2, y + 5), fill=color, width=1)
        if kind == "storm" and phase % 8 < 0.18:
            rain.line((29, 6, 25, 18, 32, 18, 26, 32), fill=(255, 235, 178, 255), width=2)
    elif kind == "snow":
        snow = ImageDraw.Draw(frame)
        for index in range(25):
            x = (index * 29 + 5 + math.sin(phase * 0.7 + index) * 4) % 64
            y = (index * 23 + phase * (2 + index % 3)) % 64
            snow.ellipse((x, y, x + (index % 3 == 0), y + (index % 3 == 0)),
                         fill=(229, 241, 233, 235))
    elif kind == "fog":
        fog = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        fog_draw = ImageDraw.Draw(fog)
        for index in range(4):
            y = 27 + index * 8 + math.sin(phase * 0.35 + index) * 2
            fog_draw.line((0, y, 63, y), fill=(206, 222, 213, 55), width=5)
        frame = Image.alpha_composite(frame, fog.filter(ImageFilter.GaussianBlur(3)))

    if show_temperature:
        overlay = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        text = f"{round(weather.temperature_c)}°"
        font = ImageFont.load_default()
        width = round(overlay_draw.textlength(text, font=font))
        overlay_draw.rounded_rectangle((2, 51, width + 8, 62), radius=3, fill=(5, 17, 30, 205))
        overlay_draw.text((5, 50), text, font=font, fill=(238, 243, 233, 255))
        frame = Image.alpha_composite(frame, overlay)
    return frame.convert("RGB")
