"""An empty chair, a fixed window, and a shadow with its own intentions."""

from dataclasses import dataclass
from functools import lru_cache
import math
import random

from PIL import Image, ImageDraw, ImageFilter


CYCLE_SECONDS = 48.0
SCALE = 3
SIZE = 64 * SCALE
Point = tuple[float, float]


def _ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def _point(point: Point) -> Point:
    return tuple(value * SCALE for value in point)


def _polygon(draw, points, fill):
    draw.polygon([_point(p) for p in points], fill=fill)


def _line(draw, points, fill, width=1):
    draw.line([_point(p) for p in points], fill=fill, width=max(1, round(width * SCALE)))


def _ellipse(draw, box, fill):
    draw.ellipse(tuple(value * SCALE for value in box), fill=fill)


@lru_cache(maxsize=1)
def _room() -> Image.Image:
    """Fixed architecture makes every independent shadow movement conspicuous."""
    image = Image.new("RGB", (SIZE, SIZE))
    pixels = []
    rng = random.Random(82)
    for y in range(SIZE):
        for x in range(SIZE):
            u, v = x / SCALE, y / SCALE
            light = math.exp(-((u - 19)**2 / 660 + (v - 19)**2 / 1050))
            noise = rng.uniform(-1.3, 1.3)
            pixels.append(tuple(round(c + light * gain + noise) for c, gain in ((49, 39), (61, 48), (60, 37))))
    image.putdata(pixels)
    draw = ImageDraw.Draw(image)
    _polygon(draw, ((0, 0), (9, 7), (9, 42), (0, 64)), (28, 42, 46))
    _polygon(draw, ((52, 7), (64, 0), (64, 64), (52, 42)), (25, 33, 39))
    _polygon(draw, ((0, 0), (64, 0), (52, 7), (9, 7)), (31, 39, 39))
    floor = Image.new("RGB", (SIZE, SIZE), (58, 64, 62))
    fd = ImageDraw.Draw(floor)
    for end in (-30, -5, 19, 43, 70, 105):
        start = 31 + (end - 31) * 12 / 34
        _line(fd, ((start, 43), (end, 64)), (30, 39, 41), .55)
    for y in (47, 53, 62):
        _line(fd, ((0, y), (64, y)), (39, 44, 45), .45)
    floor_mask = Image.new("L", (SIZE, SIZE))
    _polygon(ImageDraw.Draw(floor_mask), ((9, 42), (52, 42), (64, 64), (0, 64)), 255)
    image.paste(floor, (0, 0), floor_mask)
    _line(draw, ((9, 7), (9, 42), (52, 42), (52, 7)), (91, 104, 95), .5)
    _line(draw, ((9, 43), (52, 43)), (20, 30, 32), .6)

    # The cross of the window is projected onto the floor and never moves.
    light = Image.new("RGBA", (SIZE, SIZE))
    ld = ImageDraw.Draw(light)
    _polygon(ld, ((15, 43), (29, 43), (46, 64), (22, 64)), (167, 188, 142, 78))
    _line(ld, ((22, 43), (34, 64)), (5, 13, 17, 135), 1.2)
    _line(ld, ((18, 52), (36, 52)), (5, 13, 17, 110), 1.1)
    image = Image.alpha_composite(image.convert("RGBA"), light)
    draw = ImageDraw.Draw(image)
    # A door, left slightly open: the far side is never revealed.
    _polygon(draw, ((41, 15), (51, 15), (51, 42), (41, 42)), (9, 15, 19))
    _line(draw, ((40, 42), (40, 14), (52, 14), (52, 42)), (81, 87, 73), .65)
    _polygon(draw, ((42, 16), (48, 18), (48, 41), (42, 42)), (27, 33, 33))
    _line(draw, ((49.7, 16), (49.7, 41)), (90, 94, 64), .45)
    _ellipse(draw, (45.4, 29.7, 46.2, 30.5), (130, 125, 80))
    # Four cold panes with substantial contrast at the real LED resolution.
    _polygon(draw, ((12, 10), (27, 10), (27, 29), (12, 29)), (12, 24, 29))
    for x in (13.5, 20.5):
        for y in (11.5, 20):
            _polygon(draw, ((x, y), (x + 5, y), (x + 5, y + 6.5), (x, y + 6.5)),
                     (171, 199, 160) if y < 20 else (134, 170, 148))
    _line(draw, ((11.5, 29), (28, 29)), (173, 186, 145), 1.2)
    _line(draw, ((13, 10), (27, 10), (27, 28)), (109, 134, 112), .6)
    # Hairline damage in the plaster; static, not animated noise.
    for points in (((32, 8), (32, 11), (34, 13)), ((10, 32), (12, 35), (11, 38)),
                   ((36, 33), (35, 36), (37, 39))):
        _line(draw, points, (39, 52, 52), .4)
    return image.convert("RGB")


def _chair(image: Image.Image) -> None:
    """A small, plainly empty chair is the constant reference for the illusion."""
    draw = ImageDraw.Draw(image)
    _line(draw, ((25, 31), (25, 44)), (135, 116, 78), 1.25)
    _line(draw, ((31, 32), (31, 45)), (95, 87, 66), 1.15)
    _line(draw, ((25, 32), (31, 33)), (167, 140, 88), 1.2)
    _line(draw, ((25, 34), (31, 35)), (105, 97, 69), .85)
    _polygon(draw, ((24, 38), (30, 38), (34, 40), (27, 40)), (147, 120, 75))
    _line(draw, ((27, 40), (27, 47)), (127, 111, 76), 1)
    _line(draw, ((33, 40), (33, 46)), (82, 79, 65), 1)


@dataclass(frozen=True)
class ShadowPose:
    rise: float
    reach: float
    approach: float
    retreat: float
    tilt: float
    eyes: float
    sway: float
    chair_shadow: float


def shadow_pose(phase: float, seed: int = 17, intensity: int = 100) -> ShadowPose:
    rng = random.Random(seed)
    delay = rng.uniform(-.012, .012)
    p = phase - delay
    amount = intensity / 100
    rise = _ease((p - .09) / .23)
    reach = _ease((p - .32) / .17) * (1 - _ease((p - .72) / .08))
    approach = _ease((p - .66) / .13) * amount
    retreat = _ease((p - .81) / .055)
    twitch_at = rng.uniform(.57, .62)
    twitch = _ease((p - twitch_at) / .004) - _ease((p - twitch_at - .022) / .012)
    tilt = (-.8 * reach + .95 * _ease((p - .52) / .08) + .38 * twitch) * amount
    eyes = _ease((p - .55) / .025) * (1 - retreat)
    eyes *= 1 - .92 * (_ease((p - .63) / .004) - _ease((p - .638) / .008))
    sway = (math.sin(p * 17 + seed) * .55 + twitch * 1.4) * amount * (1 - retreat)
    chair_shadow = (1 - _ease((p - .10) / .20)) + _ease((phase - .95) / .05)
    return ShadowPose(rise, reach * amount, approach, retreat, tilt, eyes, sway, min(1, chair_shadow))


def render_shadow_frame(phase: float, *, seed: int = 17, intensity: int = 100) -> Image.Image:
    """Return a 64×64 room. Phase 0 and 1 are the same quiet, empty room."""
    if not 25 <= intensity <= 100:
        raise ValueError("Shadow intensity must be between 25 and 100.")
    phase = max(0.0, min(1.0, phase))
    pose = shadow_pose(phase, seed, intensity)
    frame = _room().copy().convert("RGBA")
    floor = Image.new("RGBA", (SIZE, SIZE))
    fd = ImageDraw.Draw(floor)
    drift = math.sin(phase * math.tau) * 1.6 * pose.chair_shadow
    _polygon(fd, ((25, 44), (31, 45), (47 + drift, 54), (42 + drift, 55)),
             (2, 6, 10, round(205 * pose.chair_shadow)))
    _line(fd, ((29, 45), (40 + drift, 53), (45 + drift, 52)),
          (2, 6, 10, round(180 * pose.chair_shadow)), 1.6)
    frame = Image.alpha_composite(frame, floor.filter(ImageFilter.GaussianBlur(.45 * SCALE)))

    if pose.rise > 0 and pose.retreat < 1:
        mask = Image.new("L", (SIZE, SIZE))
        draw = ImageDraw.Draw(mask)
        collapse = 1 - pose.retreat
        foot = (37 + pose.retreat * 9, 47 - pose.retreat * 5)
        height = (9 + 24 * pose.rise + 5 * pose.approach) * collapse
        head = (37 + pose.sway - 4 * pose.approach + pose.retreat * 9, foot[1] - height)
        shoulder = (head[0] + 1.8 * pose.reach, head[1] + 6.5 + pose.approach * 2)
        hip = (foot[0] - 1 - 4 * pose.approach, foot[1] - 11 * pose.rise * collapse)
        width = (2.4 + pose.approach * 2.8) * collapse
        _polygon(draw, ((shoulder[0] - width, shoulder[1]),
                        (shoulder[0] + width, shoulder[1] + .6),
                        (hip[0] + width * .55, hip[1]), (hip[0] - width * .55, hip[1])), 255)
        _line(draw, (head, shoulder), 255, 1.8 + pose.approach)
        _line(draw, (hip, (foot[0] - 2.8, foot[1] - 4), (foot[0] - 3.5, foot[1])), 255, 1.8)
        _line(draw, (hip, (foot[0] + 1.5, foot[1] - 5), (foot[0] + 2.5, foot[1])), 255, 1.4)

        # One arm slowly violates the silhouette; its elbow folds the wrong way.
        wrist = (head[0] - 4 - 9 * pose.reach - 7 * pose.approach,
                 shoulder[1] + 13 - 12 * pose.reach + pose.approach * 9)
        elbow = (shoulder[0] - 3 - 7 * pose.reach - 4 * pose.approach,
                 shoulder[1] + 8 - 11 * pose.reach + pose.approach * 4)
        _line(draw, ((shoulder[0] - width, shoulder[1] + 1), elbow, wrist), 255, 1.8 + pose.approach)
        other_wrist = (foot[0] + 5 + pose.approach * 10, foot[1] - 6 - pose.approach * 3)
        _line(draw, ((shoulder[0] + width, shoulder[1] + 1),
                     (foot[0] + 5, shoulder[1] + 9), other_wrist), 255, 1.45 + pose.approach)
        for finger in range(3):
            spread = (finger - 1) * (1 + pose.approach)
            _line(draw, (wrist, (wrist[0] - 2 - finger * .6, wrist[1] + spread),
                         (wrist[0] - 3 - finger * .5, wrist[1] + spread + 1.7)), 255, .65)
            if pose.approach > .1:
                _line(draw, (other_wrist, (other_wrist[0] + 1 + spread, other_wrist[1] + 3 + finger)), 255, .65)

        def on_head(x: float, y: float) -> Point:
            angle = pose.tilt
            return (head[0] + x * math.cos(angle) - y * math.sin(angle),
                    head[1] + x * math.sin(angle) + y * math.cos(angle))

        radius_x = (2.5 + 2.2 * pose.approach) * collapse
        radius_y = (4 + 1.7 * pose.approach) * collapse
        outline = [on_head(radius_x * math.cos(i * math.tau / 32),
                           radius_y * math.sin(i * math.tau / 32)) for i in range(32)]
        _polygon(draw, outline, 255)
        # The distant form is soft; it becomes unnaturally sharp as it notices us.
        mask = mask.filter(ImageFilter.GaussianBlur((.38 - .24 * pose.approach) * SCALE))
        opacity = _ease(pose.rise * 3) * collapse
        mask = mask.point(lambda value: round(value * opacity * .97))
        darkness = Image.new("RGBA", (SIZE, SIZE), (2, 5, 9, 255))
        darkness.putalpha(mask)
        frame = Image.alpha_composite(frame, darkness)

        if pose.eyes > 0:
            eyes = ImageDraw.Draw(frame)
            level = round(175 * pose.eyes * opacity * (0.5 + intensity / 200))
            for x in (-1.05 - pose.approach * .6, 1.05 + pose.approach * .6):
                ex, ey = on_head(x, -.35)
                _ellipse(eyes, (ex - .38, ey - .22, ex + .38, ey + .22),
                         (level, min(255, level + 12), round(level * .80)))

    _chair(frame)
    return frame.convert("RGB").resize((64, 64), Image.Resampling.LANCZOS)


class ShadowAnimator:
    def __init__(self, now: float = 0.0, seed: int | None = None):
        self.seed = random.SystemRandom().getrandbits(32) if seed is None else seed
        self.random = random.Random(self.seed)
        self.last_time = now
        self.active_elapsed = 0.0
        self.active = False
        self.next_appearance = now + self._quiet_interval(100, first=True)
        self.speed = 100
        self.frequency = 100

    def _quiet_interval(self, frequency: int, *, first: bool = False) -> float:
        # At 100%, appearances still have a small random pause. Lower settings
        # stretch that pause dramatically without slowing the apparition itself.
        base = 5.0 if first else 3.0
        extra = CYCLE_SECONDS * max(0.0, 100 / frequency - 1)
        return (base + extra) * self.random.uniform(.72, 1.38)

    @property
    def phase(self) -> float:
        return self.active_elapsed / CYCLE_SECONDS if self.active else 0.0

    def frame(
        self, now: float, *, speed: int = 100, frequency: int = 100, intensity: int = 100,
    ) -> Image.Image:
        elapsed = max(0, now - self.last_time)
        self.last_time = now
        if frequency != self.frequency:
            remaining = max(0, self.next_appearance - now)
            self.next_appearance = now + remaining * self.frequency / frequency
            self.frequency = frequency
        self.speed = speed
        if not self.active and now >= self.next_appearance:
            self.active = True
            self.active_elapsed = 0.0
        if self.active:
            self.active_elapsed += elapsed * self.speed / 100
            if self.active_elapsed >= CYCLE_SECONDS:
                self.active = False
                self.active_elapsed = 0.0
                self.next_appearance = now + self._quiet_interval(self.frequency)
        return render_shadow_frame(
            self.phase, seed=self.seed + round(self.next_appearance) * 491, intensity=intensity,
        )
