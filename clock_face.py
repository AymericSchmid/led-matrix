"""An analog clock drawn onto the physical panel's 64 x 64 pixel grid."""

from datetime import datetime
from functools import lru_cache
import math

from PIL import Image, ImageDraw


CENTER = (31.5, 31.5)
WHITE = (225, 228, 226)
YELLOW = (255, 191, 47)
PALETTES = {
    "light": {
        "background": (232, 228, 216),
        "hands": (27, 30, 33),
        "second": (218, 151, 25),
        "numbers": (44, 47, 49),
        "hour_ticks": (66, 69, 70),
        "minute_ticks": (130, 130, 125),
    },
    "dark": {
        "background": (0, 0, 0),
        "hands": WHITE,
        "second": YELLOW,
        "numbers": (182, 188, 192),
        "hour_ticks": (140, 147, 155),
        "minute_ticks": (63, 70, 80),
    },
}

# A tiny, deliberately pixel-aligned font keeps all twelve numbers legible.
# Each digit is 3 x 5 pixels, with one pixel between adjacent digits.
DIGITS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}


def point_on_dial(turns: float, radius: float) -> tuple[float, float]:
    """Zero points to twelve; positive turns move clockwise."""
    angle = turns * math.tau
    return CENTER[0] + math.sin(angle) * radius, CENTER[1] - math.cos(angle) * radius


def draw_number(draw: ImageDraw.ImageDraw, number: int, color: tuple[int, int, int]) -> None:
    text = str(number)
    width = len(text) * 4 - 1
    cx, cy = point_on_dial(number / 12, 24)
    left = round(cx - (width - 1) / 2)
    top = round(cy - 2)
    for digit_index, digit in enumerate(text):
        for y, row in enumerate(DIGITS[digit]):
            for x, pixel in enumerate(row):
                if pixel == "1":
                    draw.point((left + digit_index * 4 + x, top + y), fill=color)


@lru_cache(maxsize=8)
def _dial(show_numbers: bool, show_ticks: bool, theme: str) -> Image.Image:
    palette = PALETTES[theme]
    frame = Image.new("RGB", (64, 64), palette["background"])
    draw = ImageDraw.Draw(frame)

    if show_ticks:
        for minute in range(60):
            is_hour = minute % 5 == 0
            start = point_on_dial(minute / 60, 28 if is_hour else 29)
            end = point_on_dial(minute / 60, 30)
            draw.line(
                [tuple(round(v) for v in start), tuple(round(v) for v in end)],
                fill=palette["hour_ticks"] if is_hour else palette["minute_ticks"],
            )

    if show_numbers:
        for hour in range(1, 13):
            draw_number(draw, hour, palette["numbers"])

    return frame


def draw_clock(
    moment: datetime | None = None, *, show_numbers: bool = True, show_ticks: bool = True,
    hand_length_scale: float = 1.0, hand_width_scale: float = 1.0,
    theme: str = "light",
) -> Image.Image:
    """Return a fresh RGB frame; no window or matrix hardware is required."""
    moment = moment if moment is not None else datetime.now()
    if theme not in PALETTES:
        raise ValueError("Clock theme must be 'light' or 'dark'.")
    palette = PALETTES[theme]
    frame = _dial(show_numbers, show_ticks, theme).copy()

    # Draw hands at higher resolution, then sample back to the real 64 x 64
    # grid. Intermediate brightness values soften diagonals on physical LEDs.
    scale = 4
    hands = Image.new("RGBA", (64 * scale, 64 * scale))
    draw = ImageDraw.Draw(hands)

    def scaled(point: tuple[float, float]) -> tuple[float, float]:
        return tuple((value + 0.5) * scale - 0.5 for value in point)

    def hand(turns: float, length: float, width: float, color: tuple, tail: float = 0) -> None:
        start = scaled(point_on_dial(turns, -tail))
        end = scaled(point_on_dial(turns, length))
        draw.line([start, end], fill=color, width=round(width * scale))
        radius = width * scale / 2
        for x, y in (start, end):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    # Whole seconds give a deliberate once-per-second movement.
    seconds = moment.second
    minutes = moment.minute + seconds / 60
    hours = moment.hour % 12 + minutes / 60
    hand(hours / 12, 14 * hand_length_scale, 2.5 * hand_width_scale, palette["hands"])
    hand(minutes / 60, 20 * hand_length_scale, 1.6 * hand_width_scale, palette["hands"])
    hand(seconds / 60, 22 * hand_length_scale, 0.8 * hand_width_scale, palette["second"], tail=4)
    hand(seconds / 60 + 0.5, 3.5, 2 * hand_width_scale, palette["second"])

    x, y = scaled(CENTER)
    radius = max(1.5, 1.25 * hand_width_scale) * scale
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=palette["second"])

    hands = hands.resize((64, 64), Image.Resampling.LANCZOS)
    frame.paste(hands, (0, 0), hands)
    return frame
