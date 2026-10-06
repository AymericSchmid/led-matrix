"""A ceramic surface that fractures, receives gold, and quietly renews itself."""

from dataclasses import dataclass
from functools import lru_cache
import heapq
import math
import random

from PIL import Image, ImageDraw, ImageFilter


THEMES = ("indigo", "ivory")
CYCLE_SECONDS = 72.0
SCALE = 3
SIZE = 64 * SCALE
Point = tuple[float, float]


def _smooth(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def _scaled(point: Point) -> Point:
    return point[0] * SCALE, point[1] * SCALE


def _clip(polygon: list[Point], nx: float, ny: float, limit: float) -> list[Point]:
    """Clip a convex polygon to one side of a perpendicular bisector."""
    result = []
    previous = polygon[-1]
    prev_distance = previous[0] * nx + previous[1] * ny - limit
    for current in polygon:
        distance = current[0] * nx + current[1] * ny - limit
        if (distance <= 0) != (prev_distance <= 0):
            ratio = prev_distance / (prev_distance - distance)
            result.append(tuple(a + (b - a) * ratio for a, b in zip(previous, current)))
        if distance <= 0:
            result.append(current)
        previous, prev_distance = current, distance
    return result


@dataclass(frozen=True)
class Seam:
    start: Point
    end: Point
    arrival: float
    width: float
    lustre: float


@dataclass(frozen=True)
class Pattern:
    cells: tuple
    seams: tuple[Seam, ...]


@lru_cache(maxsize=4)
def _pattern(seed: int, density: int) -> Pattern:
    rng = random.Random(seed)
    sites: list[Point] = []
    for _ in range(4 + round(density / 10)):
        candidates = [(rng.uniform(2, 62), rng.uniform(2, 62)) for _ in range(6)]
        sites.append(max(candidates, key=lambda p: min(
            (math.dist(p, q) for q in sites), default=1,
        )))

    cells = []
    edges = set()
    for x, y in sites:
        polygon = [(0.0, 0.0), (64.0, 0.0), (64.0, 64.0), (0.0, 64.0)]
        for other_x, other_y in sites:
            if (x, y) != (other_x, other_y):
                polygon = _clip(polygon, other_x - x, other_y - y,
                                (other_x**2 + other_y**2 - x**2 - y**2) / 2)
        cells.append((tuple(polygon), rng.uniform(-1, 1)))
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            a, b = tuple(round(v, 4) for v in a), tuple(round(v, 4) for v in b)
            # Only internal joints: the tile's outer edge isn't a fracture.
            if any(a[axis] == b[axis] and a[axis] in (0, 64) for axis in (0, 1)):
                continue
            if math.dist(a, b) > 0.0001:
                edges.add(tuple(sorted((a, b))))

    graph: dict[Point, list] = {}
    for a, b in sorted(edges):
        length = math.dist(a, b)
        graph.setdefault(a, []).append((b, length))
        graph.setdefault(b, []).append((a, length))
    boundary = sorted(p for p in graph if any(v in (0, 64) for v in p))
    # Some joints meet the rim at both ends without touching another joint.
    # Let the propagation reach those pieces around the tile's unseen edge.
    def perimeter(point: Point) -> float:
        x, y = point
        if y == 0:
            return x
        if x == 64:
            return 64 + y
        if y == 64:
            return 192 - x
        return 256 - y

    rim = sorted(boundary, key=perimeter)
    for a, b in zip(rim, rim[1:] + rim[:1]):
        length = ((perimeter(b) - perimeter(a)) % 256) * 1.5
        graph[a].append((b, length))
        graph[b].append((a, length))
    origin = rng.choice(boundary or sorted(graph))
    distances = {origin: 0.0}
    queue = [(0.0, origin)]
    while queue:
        distance, vertex = heapq.heappop(queue)
        if distance > distances[vertex]:
            continue
        for neighbor, length in graph[vertex]:
            candidate = distance + length
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))

    seams = []
    for a, b in sorted(edges):
        length = math.dist(a, b)
        count = max(2, math.ceil(length / 1.5))
        normal = (-(b[1] - a[1]) / length, (b[0] - a[0]) / length)
        width = rng.uniform(0.8, 1.25)
        points = [a]
        for step in range(1, count):
            fraction = step / count
            offset = rng.uniform(-0.48, 0.48) * math.sin(math.pi * fraction)
            points.append(tuple(a[i] + (b[i] - a[i]) * fraction + normal[i] * offset for i in (0, 1)))
        points.append(b)
        for step, (start, end) in enumerate(zip(points, points[1:])):
            along = length * (step + 0.5) / count
            arrival = min(distances[a] + along, distances[b] + length - along)
            seams.append(Seam(start, end, arrival, width, rng.uniform(0.78, 1.0)))
    longest = max(seam.arrival for seam in seams)
    return Pattern(tuple(cells), tuple(
        Seam(s.start, s.end, s.arrival / longest, s.width, s.lustre) for s in seams
    ))


@lru_cache(maxsize=2)
def _ceramic(theme: str) -> Image.Image:
    if theme not in THEMES:
        raise ValueError("Kintsugi finish must be indigo or ivory.")
    rng = random.Random(812)
    pixels = []
    for y in range(SIZE):
        for x in range(SIZE):
            u, v = x / SIZE, y / SIZE
            wash = math.exp(-((u - 0.24)**2 + (v - 0.18)**2) / 0.48)
            grain = rng.uniform(-1.5, 1.5)
            glaze = math.sin(u * 13 + math.sin(v * 8)) * 1.1
            edge = min(x, y, SIZE - 1 - x, SIZE - 1 - y) / SCALE
            bevel = max(0, 1 - edge / 1.2)
            if theme == "indigo":
                color = (12 + wash * 17, 25 + wash * 29, 51 + wash * 48)
            else:
                color = (191 + wash * 43, 181 + wash * 42, 156 + wash * 44)
            pixels.append(tuple(round(max(0, min(255, c + grain + glaze - bevel * 9))) for c in color))
    image = Image.new("RGB", (SIZE, SIZE))
    image.putdata(pixels)
    return image


@lru_cache(maxsize=4)
def _shards(seed: int, density: int, theme: str) -> Image.Image:
    layer = Image.new("RGBA", (SIZE, SIZE))
    draw = ImageDraw.Draw(layer)
    for polygon, tint in _pattern(seed, density).cells:
        color = (174, 200, 232) if theme == "indigo" else (255, 244, 213)
        if tint < 0:
            color = (2, 7, 22) if theme == "indigo" else (78, 60, 39)
        draw.polygon([_scaled(p) for p in polygon], fill=(*color, round(abs(tint) * 19)))
    return Image.alpha_composite(_ceramic(theme).convert("RGBA"), layer).convert("RGB")


def stage_name(phase: float) -> str:
    if phase < 0.12:
        return "Fracture"
    if phase < 0.65:
        return "Gold"
    if phase < 0.82:
        return "Stillness"
    return "Renewal"


def render_kintsugi_frame(
    phase: float, *, seed: int = 7, theme: str = "indigo", density: int = 50,
) -> Image.Image:
    """Draw one cycle, from intact porcelain (0) through repair to intact (1)."""
    if not 20 <= density <= 100:
        raise ValueError("Kintsugi crack density must be between 20 and 100.")
    phase = max(0.0, min(1.0, phase))
    renewal = _smooth((phase - 0.82) / 0.17)
    presence = _smooth(phase / 0.045) * (1 - renewal)
    base = _ceramic(theme)
    if presence == 0:
        return base.resize((64, 64), Image.Resampling.LANCZOS)
    frame = Image.blend(base, _shards(seed, density, theme), presence).convert("RGBA")
    cracks = Image.new("RGBA", (SIZE, SIZE))
    gold = Image.new("RGBA", (SIZE, SIZE))
    crack_draw, gold_draw = ImageDraw.Draw(cracks), ImageDraw.Draw(gold)
    crack_front = (phase - 0.008) / 0.18
    gold_front = (phase - 0.12) / 0.50
    for seam in _pattern(seed, density).seams:
        opened = _smooth((crack_front - seam.arrival) / 0.035 + 1) * presence
        if opened <= 0:
            continue
        points = [_scaled(seam.start), _scaled(seam.end)]
        crack_color = (1, 4, 13) if theme == "indigo" else (60, 43, 26)
        crack_draw.line(points, fill=(*crack_color, round(210 * opened)), width=round(1.65 * SCALE))
        bevel = [(x + 0.45 * SCALE, y + 0.45 * SCALE) for x, y in points]
        crack_draw.line(bevel, fill=(166, 183, 192, round(48 * opened)), width=SCALE)

        filled = _smooth((gold_front - seam.arrival) / 0.045 + 1) * presence
        if filled <= 0:
            continue
        # A moving reflection along the joined surface, strongest at the pour.
        gleam = math.exp(-((gold_front - seam.arrival) / 0.065)**2)
        reflection = (0.5 + 0.5 * math.sin(seam.arrival * 13 - phase * 25)) ** 8
        lustre = seam.lustre * (0.82 + 0.18 * reflection)
        color = (255, round(167 + 55 * lustre + 20 * gleam), round(43 + 53 * lustre + 75 * gleam))
        gold_draw.line(points, fill=(191, 105, 22, round(235 * filled)), width=round((seam.width + 0.55) * SCALE))
        gold_draw.line(points, fill=(*color, round(255 * filled)), width=max(2, round(seam.width * SCALE)))
    frame = Image.alpha_composite(frame, cracks)
    glow = gold.filter(ImageFilter.GaussianBlur(1.35 * SCALE))
    glow.putalpha(glow.getchannel("A").point(lambda value: round(value * 0.42)))
    frame = Image.alpha_composite(frame, glow)
    frame = Image.alpha_composite(frame, gold)
    return frame.convert("RGB").resize((64, 64), Image.Resampling.LANCZOS)


class KintsugiAnimator:
    """Accumulate time so changing pace never jumps to another repair phase."""

    def __init__(self, now: float = 0.0, seed: int | None = None):
        self.seed = random.SystemRandom().getrandbits(32) if seed is None else seed
        self.last_time = now
        self.elapsed = 0.0
        self.speed = 100
        self.density = 50

    @property
    def phase(self) -> float:
        return (self.elapsed % CYCLE_SECONDS) / CYCLE_SECONDS

    def frame(self, now: float, *, speed: int = 100, density: int = 50, theme: str = "indigo") -> Image.Image:
        self.elapsed += max(0, now - self.last_time) * self.speed / 100
        self.last_time = now
        self.speed = speed
        if density != self.density:
            self.elapsed = 0.0
            self.density = density
        cycle = int(self.elapsed // CYCLE_SECONDS)
        return render_kintsugi_frame(
            self.phase, seed=self.seed + cycle * 1_000_003, theme=theme, density=density,
        )
