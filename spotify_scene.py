"""Background Spotify polling; UI code only reads completed RGB frames."""

from dataclasses import dataclass, field
import colorsys
import math
from queue import Empty, Full, Queue
from threading import Event, Thread
import time

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from spotify_client import SpotifyClient, SpotifyError


# Clockwise, with each corner visited once. Leave the outermost LEDs dark.
PROGRESS_PATH = (
    tuple((x, 1) for x in range(1, 63))
    + tuple((62, y) for y in range(2, 63))
    + tuple((x, 62) for x in range(61, 0, -1))
    + tuple((1, y) for y in range(61, 1, -1))
)
SPOTIFY_STYLES = ("cover", "vinyl-spin", "vinyl-still", "colour-drift")
SPOTIFY_STYLE_LABELS = {
    "cover": "COVER",
    "vinyl-spin": "VINYL + SPIN",
    "vinyl-still": "VINYL + STILL COVER",
    "colour-drift": "SOFT COLOUR DRIFT",
}


def _cover_colour_candidates(
    cover: Image.Image,
) -> list[tuple[float, int, tuple[int, int, int]]]:
    """Rank colour buckets while retaining a vivid original pixel per bucket."""
    sample = cover.convert("RGB").resize((32, 32), Image.Resampling.NEAREST)
    pixels = sample.load()
    buckets: dict[tuple[int, int, int], list[object]] = {}
    for y in range(sample.height):
        for x in range(sample.width):
            color = pixels[x, y]
            key = tuple(channel // 16 for channel in color)
            hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
            vividness = saturation * (0.35 + value)
            bucket = buckets.setdefault(key, [0, color, vividness])
            bucket[0] = int(bucket[0]) + 1
            if vividness > float(bucket[2]):
                bucket[1] = color
                bucket[2] = vividness

    candidates = []
    for count, color, _ in buckets.values():
        _, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
        if count < 3 or value < 0.06:
            continue
        score = count ** 0.5 * (0.30 + saturation) * value ** 1.1
        candidates.append((score, count, color))
    return sorted(candidates, reverse=True)


def _vibrant_lift(color: tuple[int, int, int], target_brightness: float) -> tuple[int, int, int]:
    """Raise dark colours while retaining more saturation than an RGB white mix."""
    brightness = sum(channel * weight for channel, weight in zip(color, (0.2126, 0.7152, 0.0722)))
    if brightness >= target_brightness:
        return color
    lift = (target_brightness - brightness) / (255 - brightness)
    color = tuple(round(channel + (255 - channel) * lift) for channel in color)
    hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
    saturation = min(1.0, saturation * 1.8)
    lifted = tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(hue, saturation, value))
    brightness = sum(channel * weight for channel, weight in zip(lifted, (0.2126, 0.7152, 0.0722)))
    if brightness < target_brightness:
        lift = (target_brightness - brightness) / (255 - brightness)
        lifted = tuple(round(channel + (255 - channel) * lift) for channel in lifted)
    return lifted


def album_accent(cover: Image.Image) -> tuple[int, int, int]:
    """Favor a substantial colourful region while preserving its vivid source pixel."""
    candidates = [
        (score, color) for score, count, color in _cover_colour_candidates(cover)
        if count >= 16 and colorsys.rgb_to_hsv(*(channel / 255 for channel in color))[1] >= 0.18
    ]
    if not candidates:
        return (218, 218, 218)  # Monochrome covers get a neutral border.
    return _vibrant_lift(max(candidates)[1], 135)


def _lift_palette_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Lift a sampled cover colour just enough to survive the LED grid."""
    return _vibrant_lift(color, 105)


def _brighten_drift_color(color: tuple[int, int, int]) -> tuple[int, int, int]:
    hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
    rgb = colorsys.hsv_to_rgb(hue, max(0.38, saturation), min(1.0, value * 1.28))
    return tuple(round(channel * 255) for channel in rgb)


def album_palette(cover: Image.Image, limit: int = 4) -> tuple[tuple[int, int, int], ...]:
    """Extract several distinct, visible colours directly from the album cover."""
    selected = []
    source_colors = []
    for _, _, color in _cover_colour_candidates(cover):
        if all(sum((a - b) ** 2 for a, b in zip(color, chosen)) >= 48 ** 2 for chosen in source_colors):
            source_colors.append(color)
            selected.append(_lift_palette_color(color))
        if len(selected) == limit:
            break
    if not selected:
        return (album_accent(cover),)
    return tuple(selected)


def dim_accent(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(round(channel * 0.14) for channel in color)


def drift_palette(
    color: tuple[int, int, int], *, bright: bool = False,
) -> tuple[tuple[int, int, int], ...]:
    """Build a small, harmonious palette from the album's dominant accent."""
    hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
    saturation = max(0.32, saturation)
    value = min(1.0, value * (1.28 if bright else 1.0))
    palette = []
    for shift, sat_scale, value_scale in ((0.0, 1.0, 1.0), (0.12, 0.88, 0.92),
                                           (-0.16, 0.78, 0.84), (0.42, 0.58, 0.78)):
        rgb = colorsys.hsv_to_rgb(
            (hue + shift) % 1.0,
            min(1.0, saturation * sat_scale),
            min(1.0, value * value_scale),
        )
        palette.append(tuple(round(channel * 255) for channel in rgb))
    return tuple(palette)


def waiting_frame() -> Image.Image:
    frame = Image.new("RGB", (64, 64), (5, 10, 8))
    draw = ImageDraw.Draw(frame)
    draw.ellipse((8, 8, 55, 55), outline=(20, 40, 30))
    draw.line((27, 40, 27, 23, 42, 20, 42, 37), fill=(70, 185, 118), width=2)
    draw.line((27, 27, 42, 24), fill=(70, 185, 118), width=2)
    draw.ellipse((20, 37, 28, 43), fill=(70, 185, 118))
    draw.ellipse((35, 34, 43, 40), fill=(70, 185, 118))
    return frame


@dataclass
class SpotifyView:
    frame: Image.Image
    caption: str
    spotify_url: str = ""
    needs_login: bool = False
    connecting: bool = False
    progress_ms: int | None = None
    duration_ms: int | None = None
    is_playing: bool = False
    sampled_at: float = field(default_factory=time.monotonic)
    accent_color: tuple[int, int, int] | None = None
    palette: tuple[tuple[int, int, int], ...] | None = None

    def __post_init__(self):
        # Extract once per incoming view, never on each animation frame.
        if self.accent_color is None:
            self.accent_color = album_accent(self.frame)
        if self.palette is None:
            self.palette = album_palette(self.frame)


def playback_progress(view: SpotifyView, now: float | None = None) -> float | None:
    """Interpolate locally between polls, then accept each new Spotify sample."""
    if view.progress_ms is None or view.duration_ms is None or view.duration_ms <= 0:
        return None
    position = view.progress_ms
    if view.is_playing:
        now = time.monotonic() if now is None else now
        position += max(0, now - view.sampled_at) * 1000
    return max(0, min(1, position / view.duration_ms))


def vinyl_needle_tip(view: SpotifyView, now: float | None = None) -> tuple[int, int]:
    """Return the stylus position, moving from the label toward the outer groove."""
    progress = playback_progress(view, now)
    progress = 0.0 if progress is None else progress
    # The requested animation starts close to the label and travels outward.
    radius = 10.0 + progress * 16.0
    angle = math.radians(24 + progress * 18)
    return round(31.5 + math.cos(angle) * radius), round(31.5 + math.sin(angle) * radius)


def vinyl_tonearm_points(
    view: SpotifyView, now: float | None = None,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Return pivot, elbow, and stylus points for the rotating tonearm assembly."""
    tip = vinyl_needle_tip(view, now)
    pivot = (54, 8)
    dx, dy = tip[0] - pivot[0], tip[1] - pivot[1]
    distance = max(1.0, math.hypot(dx, dy))
    elbow_distance = min(19.0, distance - 2.0)
    perpendicular = (-dy / distance * 2.5, dx / distance * 2.5)
    elbow = (
        round(pivot[0] + dx / distance * elbow_distance + perpendicular[0]),
        round(pivot[1] + dy / distance * elbow_distance + perpendicular[1]),
    )
    return pivot, elbow, tip


def draw_vinyl_reflection(frame: Image.Image, rotation_degrees: float) -> None:
    """Paint a soft diagonal sheen that rotates clockwise with the record."""
    mask = Image.new("L", frame.size, 0)
    draw = ImageDraw.Draw(mask)
    # Several offset strokes make one broad, feathered diagonal reflection.
    draw.line((4, 53, 39, 18), fill=120, width=2)
    draw.line((2, 51, 40, 13), fill=70, width=7)
    draw.line((10, 60, 47, 23), fill=38, width=11)
    # Pillow's positive angles turn counterclockwise; the album label below
    # uses the same negative angle so both parts turn together on screen.
    mask = mask.rotate(-rotation_degrees % 360, resample=Image.Resampling.BICUBIC)
    mask = mask.filter(ImageFilter.GaussianBlur(0.8))

    disc = Image.new("L", frame.size, 0)
    ImageDraw.Draw(disc).ellipse((4, 4, 59, 59), fill=255)
    mask = ImageChops.multiply(mask, disc)
    frame.paste((144, 151, 149), mask=mask)


def render_vinyl_frame(
    view: SpotifyView, rotation_degrees: float = 0.0, *, spin_cover: bool = True,
    now: float | None = None,
) -> Image.Image:
    """Draw a compact record player; the center artwork can rotate independently."""
    frame = Image.new("RGB", (64, 64), (4, 6, 9))
    draw = ImageDraw.Draw(frame)
    center = (31.5, 31.5)
    accent = view.accent_color

    draw.ellipse((3, 3, 60, 60), fill=(5, 7, 10), outline=(28, 35, 41))
    draw.ellipse((4, 4, 59, 59), fill=(9, 12, 16), outline=(48, 54, 57))
    draw.ellipse((6, 6, 57, 57), outline=(23, 29, 34))
    for radius in range(12, 27, 2):
        shade = 18 + radius % 6
        draw.ellipse(
            (center[0] - radius, center[1] - radius,
             center[0] + radius, center[1] + radius),
            outline=(shade, shade + 3, shade + 6),
        )

    angle = rotation_degrees % 360
    draw_vinyl_reflection(frame, angle)

    label = view.frame.resize((20, 20), Image.Resampling.LANCZOS)
    if spin_cover:
        label = label.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=False)
    label_mask = Image.new("L", (20, 20), 0)
    ImageDraw.Draw(label_mask).ellipse((0, 0, 19, 19), fill=255)
    frame.paste(label, (22, 22), label_mask)
    draw.ellipse((21, 21, 42, 42), outline=(5, 7, 9))
    draw.ellipse((22, 22, 41, 41), outline=(43, 47, 47))
    draw.ellipse((30, 30, 33, 33), fill=(222, 215, 179), outline=(5, 7, 9))

    # The whole tonearm rotates around its fixed pivot as the stylus travels.
    pivot, elbow, tip = vinyl_tonearm_points(view, now)
    draw.line((pivot, elbow), fill=(185, 189, 185), width=2)
    draw.line((elbow, tip), fill=(185, 189, 185), width=1)
    draw.ellipse((pivot[0] - 2, pivot[1] - 2, pivot[0] + 2, pivot[1] + 2), fill=(185, 189, 185))
    draw.ellipse((tip[0] - 1, tip[1] - 1, tip[0] + 1, tip[1] + 1), fill=accent)
    return frame


def render_colour_drift_frame(
    view: SpotifyView, rotation_degrees: float = 0.0, *, now: float | None = None,
    show_cover: bool = True, blackness: float = 0.35,
) -> Image.Image:
    """Draw the album over soft, slowly drifting colour fields."""
    blackness = max(0.0, min(1.0, blackness))
    colour_strength = 1.0 - blackness
    progress = playback_progress(view, now)
    progress = 0.0 if progress is None else progress
    phase = progress * math.tau
    if view.is_playing:
        # The local clock keeps the animation fluid between Spotify polls.
        phase += math.radians(rotation_degrees) * 0.5

    palette = view.palette or album_palette(view.frame)
    average = tuple(round(sum(color[channel] for color in palette) / len(palette)) for channel in range(3))
    background = tuple(round(3 + channel * 0.18 * colour_strength) for channel in average)
    frame = Image.new("RGB", (64, 64), background)
    ambient = Image.new("RGBA", (64, 64), (*average, round(65 * colour_strength)))
    frame = Image.alpha_composite(frame.convert("RGBA"), ambient).convert("RGB")
    glow = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    if not show_cover:
        palette = tuple(_brighten_drift_color(color) for color in palette)
    for index, color in enumerate(palette):
        orbit = phase * (0.72 + index * 0.09) + index * math.tau / 4
        x = 31.5 + math.cos(orbit) * (24 + index * 2)
        y = 31.5 + math.sin(orbit * 1.12) * (23 - index)
        pulse = 0.5 + 0.5 * math.sin(phase * 1.3 + index)
        radius = 13 + round(pulse * 5)
        if show_cover:
            alpha = 48 + round(colour_strength * 80) + (round(pulse * 30) if view.is_playing else 10)
        else:
            alpha = 86 + round(colour_strength * 120) + (round(pulse * 45) if view.is_playing else 18)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*color, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(6))
    frame = Image.alpha_composite(frame.convert("RGBA"), glow).convert("RGB")

    if show_cover:
        cover = view.frame.resize((34, 34), Image.Resampling.LANCZOS)
        frame.paste(cover, (15, 15))
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle((14, 14, 49, 49), radius=3, outline=(19, 22, 28), width=1)
    return frame


def render_spotify_frame(
    view: SpotifyView, now: float | None = None, *, show_progress: bool = True,
    style: str = "cover", rotation_degrees: float = 0.0, show_drift_cover: bool = True,
    drift_blackness: float = 0.35,
) -> Image.Image:
    if style not in SPOTIFY_STYLES:
        raise ValueError(f"Unknown Spotify style: {style}")
    if style == "vinyl-spin":
        return render_vinyl_frame(view, rotation_degrees, spin_cover=True, now=now)
    if style == "vinyl-still":
        return render_vinyl_frame(view, rotation_degrees, spin_cover=False, now=now)
    if style == "colour-drift":
        return render_colour_drift_frame(
            view, rotation_degrees, now=now, show_cover=show_drift_cover,
            blackness=drift_blackness,
        )
    if not show_progress:
        return view.frame.copy()
    progress = playback_progress(view, now)
    if progress is None:
        return view.frame.copy()

    frame = Image.new("RGB", (64, 64), "black")
    # The entire cover remains visible, with two dark pixels separating it
    # from the one-pixel progress track. Never draw over the artwork.
    frame.paste(view.frame.resize((56, 56), Image.Resampling.LANCZOS), (4, 4))
    filled = progress * len(PROGRESS_PATH)
    complete = int(filled)
    accent = view.accent_color
    unfilled = dim_accent(accent)
    for index, point in enumerate(PROGRESS_PATH):
        color = accent if index < complete else unfilled
        if index == complete:
            fraction = filled - complete
            color = tuple(round(dim + (bright - dim) * fraction)
                          for dim, bright in zip(unfilled, accent))
        frame.putpixel(point, color)
    return frame


class SpotifyWorker:
    def __init__(self, client: SpotifyClient, *, headless: bool = False):
        self.client = client
        self.headless = headless
        self.views: Queue[SpotifyView] = Queue(maxsize=1)
        self.stop = Event()
        self.wake = Event()
        self.login_requested = Event()
        self.thread = Thread(target=self._run, daemon=True, name="spotify-poll")

    def start(self) -> None:
        self.thread.start()

    def connect(self) -> None:
        self.login_requested.set()
        self.wake.set()

    def close(self) -> None:
        self.stop.set()
        self.wake.set()

    def latest(self) -> SpotifyView | None:
        try:
            return self.views.get_nowait()
        except Empty:
            return None

    def _publish(self, view: SpotifyView) -> None:
        try:
            self.views.put_nowait(view)
        except Full:
            self.latest()
            self.views.put_nowait(view)

    def poll_once(self) -> SpotifyView:
        track = self.client.currently_playing()
        if track is None:
            return SpotifyView(waiting_frame(), "Nothing playing on Spotify\nPlay a song on any of your devices.")
        frame = self.client.cover(track.image_url) if track.image_url else waiting_frame()
        state = "PLAYING" if track.is_playing else "PAUSED"
        if not track.image_url:
            state += " · NO COVER AVAILABLE"
        return SpotifyView(
            frame, f"{track.title}\n{track.artist}\n{state} · Spotify", track.spotify_url,
            progress_ms=track.progress_ms if track.image_url else None,
            duration_ms=track.duration_ms, is_playing=track.is_playing,
            # Include time spent downloading the cover in the local estimate.
            sampled_at=track.received_at,
        )

    def _run(self) -> None:
        while not self.stop.is_set():
            self.wake.clear()
            try:
                if self.login_requested.is_set():
                    self.login_requested.clear()
                    self._publish(SpotifyView(waiting_frame(), "Finish signing in in your browser…", connecting=True))
                    if self.headless:
                        self.client.authorize(self.stop, open_browser=False)
                    else:
                        self.client.authorize(self.stop)
                    if self.stop.is_set():
                        return
                self._publish(self.poll_once())
                delay = 5
            except SpotifyError as error:
                self._publish(SpotifyView(waiting_frame(), str(error), needs_login=error.needs_login))
                if error.needs_login:
                    # A revoked login waits for the user instead of retrying credentials.
                    while not self.stop.is_set() and not self.login_requested.is_set():
                        self.wake.wait(0.5)
                        self.wake.clear()
                    continue
                delay = error.retry_after
            except (OSError, ValueError, KeyError, TypeError):
                self._publish(SpotifyView(waiting_frame(), "Could not update Spotify. Check the connection and local storage."))
                delay = 15
            self.wake.wait(delay)
