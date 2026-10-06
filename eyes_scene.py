"""Uneasy, independently blinking eyes for a 64 × 64 LED panel."""

from dataclasses import dataclass
from functools import lru_cache
import math
import random

from PIL import Image, ImageDraw, ImageFilter


@dataclass(frozen=True)
class EyesPose:
    gaze_x: float = 0.0
    gaze_y: float = 0.0
    left_open: float = 1.0
    right_open: float = 1.0


class EyesAnimator:
    """Schedule irregular gaze shifts, blinks, occasional winks and double blinks."""

    def __init__(
        self, now: float = 0.0, seed: int | None = None, *,
        movement_rate: float = 1.0, blink_rate: float = 1.0,
        movement_frequency: float | None = None, movement_speed: float | None = None,
    ) -> None:
        movement_frequency = movement_rate if movement_frequency is None else movement_frequency
        movement_speed = movement_rate if movement_speed is None else movement_speed
        if movement_frequency <= 0 or movement_speed <= 0 or blink_rate <= 0:
            raise ValueError("Eye animation rates must be positive.")
        self.random = random.Random(seed)
        self.movement_rate = movement_frequency  # Backwards-compatible alias.
        self.movement_frequency = movement_frequency
        self.movement_speed = movement_speed
        self.blink_rate = blink_rate
        self.gaze_from = (0.0, 0.0)
        self.gaze_to = (0.0, 0.0)
        self.move_start = now
        self.move_end = now
        self.next_move = now + self.random.uniform(0.5, 2.4) / movement_frequency
        self.blink_start = float("-inf")
        self.blink_duration = 0.32
        self.next_blink = now + self.random.uniform(2.0, 5.5) / blink_rate
        self.blink_eye = "both"
        self.double_pending = False

    def set_rates(
        self, movement_rate: float | None = None, blink_rate: float | None = None, now: float = 0.0,
        *, movement_frequency: float | None = None, movement_speed: float | None = None,
    ) -> None:
        """Retiming upcoming actions keeps the current gaze and blink intact."""
        if movement_frequency is None:
            movement_frequency = movement_rate if movement_rate is not None else self.movement_frequency
        if movement_speed is None:
            movement_speed = movement_rate if movement_rate is not None else self.movement_speed
        if blink_rate is None:
            blink_rate = self.blink_rate
        if movement_frequency <= 0 or movement_speed <= 0 or blink_rate <= 0:
            raise ValueError("Eye animation rates must be positive.")
        if (movement_frequency, movement_speed, blink_rate) == (
            self.movement_frequency, self.movement_speed, self.blink_rate
        ):
            return
        self.pose(now)
        if movement_frequency != self.movement_frequency:
            factor = self.movement_frequency / movement_frequency
            self.next_move = now + (self.next_move - now) * factor
            self.movement_frequency = movement_frequency
            self.movement_rate = movement_frequency
        if movement_speed != self.movement_speed:
            factor = self.movement_speed / movement_speed
            if now < self.move_end:
                current_gaze = self._gaze_at(now)
                self.gaze_from = current_gaze
                self.move_start = now
                self.move_end = now + (self.move_end - now) * factor
            self.movement_speed = movement_speed
        if blink_rate != self.blink_rate:
            factor = self.blink_rate / blink_rate
            anchor = max(now, self.blink_start + self.blink_duration)
            self.next_blink = anchor + max(0, self.next_blink - anchor) * factor
            self.blink_rate = blink_rate

    def _gaze_at(self, now: float) -> tuple[float, float]:
        if now >= self.move_end:
            return self.gaze_to
        progress = max(0.0, min(1.0, (now - self.move_start) /
                                (self.move_end - self.move_start)))
        eased = progress * progress * (3 - 2 * progress)
        return tuple(a + (b - a) * eased for a, b in zip(self.gaze_from, self.gaze_to))

    def pose(self, now: float) -> EyesPose:
        while now >= self.next_move:
            event_time = self.next_move
            self.gaze_from = self._gaze_at(event_time)
            self.gaze_to = (self.random.uniform(-4.6, 4.6), self.random.uniform(-2.2, 2.2))
            self.move_start = event_time
            self.move_end = event_time + self.random.uniform(0.12, 0.55) / self.movement_speed
            self.next_move = self.move_end + self.random.uniform(0.55, 3.4) / self.movement_frequency

        while now >= self.next_blink:
            event_time = self.next_blink
            self.blink_start = event_time
            self.blink_duration = self.random.uniform(0.28, 0.42)
            self.blink_eye = self.random.choices(("both", "left", "right"), (9, 1, 1))[0]
            if not self.double_pending and self.random.random() < 0.23:
                self.next_blink = event_time + self.blink_duration + self.random.uniform(0.12, 0.22) / self.blink_rate
                self.double_pending = True
            else:
                self.next_blink = event_time + self.blink_duration + self.random.uniform(2.2, 6.0) / self.blink_rate
                self.double_pending = False

        gaze_x, gaze_y = self._gaze_at(now)
        progress = (now - self.blink_start) / self.blink_duration
        closure = math.sin(math.pi * progress) ** 2 if 0 <= progress <= 1 else 0.0
        left_open = 1 - closure if self.blink_eye in ("both", "left") else 1.0
        right_open = 1 - closure if self.blink_eye in ("both", "right") else 1.0
        return EyesPose(gaze_x, gaze_y, left_open, right_open)


@lru_cache(maxsize=1)
def _background() -> Image.Image:
    frame = Image.new("RGB", (64, 64), (12, 4, 6))
    glow = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(glow)
    draw.ellipse((-3, 15, 36, 52), fill=(116, 151, 89, 95))
    draw.ellipse((28, 15, 67, 52), fill=(116, 151, 89, 95))
    glow = glow.filter(ImageFilter.GaussianBlur(8))
    frame = Image.alpha_composite(frame.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(frame)
    draw.polygon(((29, 12), (35, 12), (36, 23), (33, 29), (30, 25)), fill=(38, 54, 36))
    return frame


def _eye_points(cx: int, cy: int, openness: float) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    upper = [
        (cx - 13, cy), (cx - 10, round(cy - 4 * openness)),
        (cx - 5, round(cy - 7 * openness)), (cx + 2, round(cy - 7 * openness)),
        (cx + 9, round(cy - 4 * openness)), (cx + 13, cy),
    ]
    lower = [
        (cx + 10, round(cy + 3 * openness)), (cx + 4, round(cy + 6 * openness)),
        (cx - 3, round(cy + 6 * openness)), (cx - 10, round(cy + 3 * openness)),
    ]
    return upper, lower


def render_eyes_frame(pose: EyesPose) -> Image.Image:
    """Draw the creature at panel resolution, retaining strong LED-scale shapes."""
    frame = _background().copy()
    draw = ImageDraw.Draw(frame)
    for index, (cx, openness) in enumerate(((17, pose.left_open), (47, pose.right_open))):
        cy = 31
        openness = max(0.0, min(1.0, openness))
        # Rough pale skin around a slit-like opening; the upper shadow is heavy.
        draw.polygon(((cx - 15, 22), (cx - 8, 17), (cx + 5, 18), (cx + 15, 25),
                      (cx + 13, 41), (cx + 4, 44), (cx - 10, 41), (cx - 15, 34)),
                     fill=(47, 67, 45))
        upper, lower = _eye_points(cx, cy, openness)
        mask = Image.new("L", (64, 64))
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.polygon(upper + lower, fill=255)
        eye = Image.new("RGB", (64, 64), (172, 199, 147))
        eye_draw = ImageDraw.Draw(eye)
        iris_x = round(cx + pose.gaze_x)
        iris_y = round(cy + pose.gaze_y)
        eye_draw.ellipse((iris_x - 5, iris_y - 6, iris_x + 5, iris_y + 6),
                         fill=(56, 88, 61), outline=(117, 149, 99))
        eye_draw.ellipse((iris_x - 2, iris_y - 5, iris_x + 2, iris_y + 5), fill=(5, 12, 9))
        eye_draw.point((iris_x - 1, iris_y - 3), fill=(196, 226, 167))
        # Uneven halftone flecks echo the reference's printed, eerie texture.
        for y in range(23, 39):
            for x in range(max(0, cx - 13), min(64, cx + 14)):
                if (x * 11 + y * 7 + index * 3) % 19 == 0 and mask.getpixel((x, y)):
                    if abs(x - iris_x) > 5 or abs(y - iris_y) > 6:
                        eye.putpixel((x, y), (94, 119, 84))
        frame.paste(eye, (0, 0), mask)
        draw = ImageDraw.Draw(frame)
        draw.line(upper, fill=(8, 10, 8), width=3)
        draw.line(lower[::-1], fill=(79, 104, 71), width=1)
        draw.polygon(((cx - 15, 25), (cx - 10, 19), (cx - 2, 18), (cx + 9, 21),
                      (cx + 15, 26), (cx + 10, 24), (cx + 2, 22), (cx - 8, 23)),
                     fill=(9, 9, 8))
        if openness < 0.15:
            draw.line((cx - 10, cy, cx + 10, cy), fill=(110, 139, 97), width=1)
    return frame
