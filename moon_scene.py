"""A textured, nearly full-panel Moon lit for the current or next night."""

from datetime import datetime, timezone
from functools import lru_cache
import math
import random

from PIL import Image, ImageDraw, ImageFilter


# USNO lists this new moon at 2026-10-10 15:50 UTC. A mean lunation keeps
# the visual phase close without a network request; individual cycles vary.
REFERENCE_NEW_MOON = datetime(2026, 10, 10, 15, 50, tzinfo=timezone.utc)
SYNODIC_MONTH_DAYS = 29.530588853
TEXTURE_SIZE = 192

PHASE_NAMES = (
    "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
    "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent",
)


def nighttime_reference(now: datetime | None = None) -> datetime:
    """Use midnight for the ongoing early-morning night, otherwise 22:00 tonight."""
    local_now = now if now is not None else datetime.now()
    hour = 0 if local_now.hour < 6 else 22
    return local_now.replace(hour=hour, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def moon_phase(when: datetime) -> float:
    """Fraction of a mean lunation since new moon: 0 new, 0.5 full."""
    if when.tzinfo is None:
        raise ValueError("Moon phase requires a timezone-aware time.")
    days = (when.astimezone(timezone.utc) - REFERENCE_NEW_MOON).total_seconds() / 86400
    return (days / SYNODIC_MONTH_DAYS) % 1.0


def phase_name(phase: float) -> str:
    return PHASE_NAMES[round(phase * 8) % 8]


def illumination(phase: float) -> float:
    return (1 - math.cos(2 * math.pi * phase)) / 2


@lru_cache(maxsize=1)
def _surface_texture() -> Image.Image:
    """A fixed lunar nearside: broad maria, fine grain, and crater rims."""
    size = TEXTURE_SIZE
    rng = random.Random(24819)

    def noise_grid(side: int) -> Image.Image:
        grid = Image.new("L", (side, side))
        grid.putdata([rng.randrange(256) for _ in range(side * side)])
        return grid.resize((size, size), Image.Resampling.BICUBIC)

    broad, fine = noise_grid(13), noise_grid(48)
    maria = Image.new("L", (size, size))
    draw = ImageDraw.Draw(maria)
    # Large, overlapping dark regions echo the familiar near-side seas.
    for x, y, rx, ry in (
        (-0.48, -0.25, 0.36, 0.21), (-0.25, -0.52, 0.25, 0.18),
        (0.20, -0.45, 0.23, 0.15), (0.34, -0.16, 0.27, 0.17),
        (0.17, 0.13, 0.25, 0.20), (-0.42, 0.31, 0.27, 0.18),
        (-0.05, 0.48, 0.22, 0.15), (0.67, -0.12, 0.12, 0.14),
    ):
        cx, cy = (x + 1) * size / 2, (y + 1) * size / 2
        draw.ellipse((cx - rx * size / 2, cy - ry * size / 2,
                      cx + rx * size / 2, cy + ry * size / 2), fill=255)
    maria = maria.filter(ImageFilter.GaussianBlur(6))

    craters = (
        (-0.58, 0.48, 0.115), (-0.26, 0.65, 0.10), (0.43, 0.53, 0.12),
        (0.60, 0.20, 0.075), (-0.72, -0.05, 0.075), (0.05, -0.72, 0.07),
        (-0.08, 0.22, 0.065), (0.74, -0.41, 0.045), (-0.34, -0.78, 0.04),
        (0.29, 0.76, 0.055), (-0.64, -0.61, 0.045), (0.52, -0.67, 0.05),
    )
    craters += tuple(
        (x, y, rng.uniform(0.018, 0.044))
        for x, y in ((rng.uniform(-0.82, 0.82), rng.uniform(-0.82, 0.82)) for _ in range(28))
        if x * x + y * y < 0.75
    )
    texture = Image.new("L", (size, size))
    pixels = texture.load()
    bp, fp, mp = broad.load(), fine.load(), maria.load()
    for py in range(size):
        y = (py + 0.5 - size / 2) / (size / 2)
        for px in range(size):
            x = (px + 0.5 - size / 2) / (size / 2)
            value = 218 + (bp[px, py] - 128) * 0.23 + (fp[px, py] - 128) * 0.08
            value -= mp[px, py] * 0.30
            for cx, cy, radius in craters:
                distance = math.hypot(x - cx, y - cy)
                if distance < radius * 1.65:
                    ring = math.exp(-((distance - radius) / (radius * 0.16)) ** 2)
                    floor = math.exp(-(distance / (radius * 0.72)) ** 4)
                    value += 36 * ring - 22 * floor
            pixels[px, py] = round(max(78, min(255, value)))
    return texture


def render_moon_frame(phase: float) -> Image.Image:
    """Render a detailed Moon into the actual 64 × 64 matrix frame."""
    size = TEXTURE_SIZE
    disk_radius = size * 0.458  # About 59 of 64 LEDs across.
    center = (size - 1) / 2
    lit_fraction = illumination(phase)
    frame = Image.new("RGB", (size, size), (3, 8, 21))
    glow = Image.new("RGBA", (size, size))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((center - disk_radius, center - disk_radius,
                       center + disk_radius, center + disk_radius),
                      fill=(105, 137, 188, round(34 + 68 * lit_fraction)))
    frame = Image.alpha_composite(frame.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(11)))

    surface = _surface_texture().load()
    pixels = frame.load()
    sun_x = math.sin(2 * math.pi * phase)
    sun_z = -math.cos(2 * math.pi * phase)
    for py in range(size):
        y = (py - center) / disk_radius
        for px in range(size):
            x = (px - center) / disk_radius
            radius_squared = x * x + y * y
            if radius_squared >= 1:
                continue
            z = math.sqrt(1 - radius_squared)
            light = sun_x * x + sun_z * z
            lit = max(0.0, min(1.0, (light + 0.025) / 0.075))
            lit = lit * lit * (3 - 2 * lit)
            if lit_fraction < 0.001:
                lit = 0.0
            relief = surface[px, py] / 210
            limb = 0.70 + 0.30 * z
            dark = (30, 43, 66)
            bright = (225, 226, 211)
            pixels[px, py] = tuple(round(min(255, (a * (1 - lit) + b * lit) * relief * limb))
                                   for a, b in zip(dark, bright)) + (255,)
    return frame.convert("RGB").resize((64, 64), Image.Resampling.LANCZOS)
