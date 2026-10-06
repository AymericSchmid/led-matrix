"""A moonlit, left-facing walker at the panel's true 64 × 64 resolution."""

import math

from PIL import Image, ImageDraw, ImageFilter


STEP_SECONDS = 1.35


def _jointed_line(draw: ImageDraw.ImageDraw, points: tuple[tuple[float, float], ...],
                  color: tuple[int, int, int, int], width: int) -> None:
    rounded = [(round(x), round(y)) for x, y in points]
    draw.line(rounded, fill=color, width=width, joint="curve")
    radius = width // 2
    for x, y in rounded:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def _night_street(phase: float) -> Image.Image:
    frame = Image.new("RGB", (64, 64))
    pixels = frame.load()
    for y in range(64):
        for x in range(64):
            horizon = y / 63
            halo = max(0, 1 - math.hypot((x - 31) / 30, (y - 29) / 36))
            pixels[x, y] = (
                round(4 + 6 * horizon + 7 * halo),
                round(9 + 11 * horizon + 16 * halo),
                round(20 + 16 * horizon + 18 * halo),
            )
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 57, 63, 63), fill=(10, 21, 33))
    draw.line((0, 58, 63, 58), fill=(38, 65, 78), width=1)
    scroll = (phase * 3.2) % 19
    for index in range(-2, 6):
        x = round(index * 19 + scroll)
        draw.line((x, 60, x + 5, 60), fill=(47, 77, 86), width=1)
        draw.point((x + 9, 55), fill=(23, 47, 62))
    return frame


def render_walker_frame(phase: float) -> Image.Image:
    """The figure faces left; moving ground and alternating limbs imply travel."""
    stride = math.sin(phase)
    bob = -round(1.2 * abs(math.cos(phase)))
    cx = 32 + round(math.sin(phase / 2))
    near_lift = 3 * max(0, math.cos(phase))
    far_lift = 3 * max(0, -math.cos(phase))
    flutter = 2 * math.sin(phase + 0.5)

    background = _night_street(phase)
    shadow = Image.new("RGBA", (64, 64))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse((16, 56, 51, 62), fill=(1, 7, 13, 140))
    background = Image.alpha_composite(background.convert("RGBA"), shadow)

    figure = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(figure)

    # The farther leg is blue-gray; each shoe keeps pointing left.
    far_foot = (cx + 4 + 11 * stride, 59 + bob - far_lift)
    _jointed_line(draw, ((cx + 3, 41 + bob), (cx + 4 + 5 * stride, 50 + bob - far_lift / 2),
                         far_foot), (85, 136, 158, 255), 5)
    fx, fy = round(far_foot[0]), round(far_foot[1])
    draw.polygon(((fx - 5, fy - 1), (fx + 2, fy - 1), (fx + 3, fy + 2), (fx - 5, fy + 2)),
                 fill=(129, 176, 185, 255))

    # The rear arm peeks out behind a long, wind-shifted coat.
    _jointed_line(draw, ((cx + 4, 25 + bob), (cx + 8 - 3 * stride, 33 + bob),
                         (cx + 9 - 6 * stride, 41 + bob)), (91, 144, 163, 255), 4)
    draw.polygon(((cx - 3, 23 + bob), (cx + 5, 22 + bob), (cx + 9, 29 + bob),
                  (cx + 9 + flutter, 44 + bob), (cx + 3, 47 + bob),
                  (cx - 6, 44 + bob), (cx - 7, 32 + bob)),
                 fill=(87, 139, 159, 255))
    draw.polygon(((cx + 3, 24 + bob), (cx + 8, 29 + bob),
                  (cx + 9 + flutter, 44 + bob), (cx + 3, 46 + bob)),
                 fill=(48, 93, 119, 255))
    draw.line((cx - 5, 28 + bob, cx - 5, 42 + bob), fill=(166, 204, 207, 255), width=2)
    draw.line((cx - 5, 38 + bob, cx + 5, 39 + bob), fill=(44, 82, 103, 255), width=2)

    near_foot = (cx - 2 - 11 * stride, 59 + bob - near_lift)
    _jointed_line(draw, ((cx - 2, 41 + bob), (cx - 2 - 5 * stride, 50 + bob - near_lift / 2),
                         near_foot), (187, 221, 221, 255), 5)
    nx, ny = round(near_foot[0]), round(near_foot[1])
    draw.polygon(((nx - 5, ny - 1), (nx + 2, ny - 1), (nx + 3, ny + 2), (nx - 5, ny + 2)),
                 fill=(220, 231, 216, 255))

    # One visible face in profile, with a small nose and a shadowed hairline.
    draw.rectangle((cx - 2, 18 + bob, cx + 2, 24 + bob), fill=(176, 208, 202, 255))
    draw.polygon(((cx - 3, 8 + bob), (cx + 3, 7 + bob), (cx + 6, 11 + bob),
                  (cx + 6, 17 + bob), (cx + 2, 20 + bob), (cx - 4, 18 + bob),
                  (cx - 5, 16 + bob), (cx - 8, 15 + bob), (cx - 8, 13 + bob),
                  (cx - 5, 12 + bob)), fill=(204, 226, 215, 255))
    draw.polygon(((cx - 5, 11 + bob), (cx - 3, 7 + bob), (cx + 3, 6 + bob),
                  (cx + 7, 10 + bob), (cx + 6, 14 + bob), (cx + 3, 11 + bob),
                  (cx - 2, 10 + bob)), fill=(47, 77, 94, 255))
    draw.point((cx - 5, 14 + bob), fill=(19, 39, 49, 255))

    # The near arm counters the forward foot; a warm scarf trails behind.
    _jointed_line(draw, ((cx - 4, 26 + bob), (cx - 7 + 3 * stride, 33 + bob),
                         (cx - 9 + 6 * stride, 41 + bob)), (173, 210, 211, 255), 4)
    draw.polygon(((cx - 4, 22 + bob), (cx + 3, 21 + bob), (cx + 6, 25 + bob),
                  (cx - 1, 27 + bob), (cx - 5, 25 + bob)), fill=(230, 180, 104, 255))
    draw.polygon(((cx + 3, 23 + bob), (cx + 11 + flutter, 21 + bob),
                  (cx + 16 + flutter, 24 + bob), (cx + 7, 27 + bob)),
                 fill=(191, 125, 74, 255))
    draw.line((cx + 6, 25 + bob, cx + 13 + flutter, 24 + bob),
              fill=(248, 201, 119, 255), width=1)

    bloom = figure.getchannel("A").filter(ImageFilter.GaussianBlur(2.3))
    bloom = bloom.point(lambda value: round(value * 0.34))
    light = Image.new("RGBA", (64, 64), (88, 160, 190, 0))
    light.putalpha(bloom)
    background = Image.alpha_composite(background, light)
    return Image.alpha_composite(background, figure).convert("RGB")
