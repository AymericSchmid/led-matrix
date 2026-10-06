"""Persistent state and one render loop shared by matrix and phone preview."""

from argparse import Namespace
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from io import BytesIO
from pathlib import Path
import json
import sqlite3
from threading import Event, RLock, Thread

from PIL import Image

from headless_scene import HeadlessScene
from weather_scene import DEFAULT_CITY


SCENES = ("clock", "circle", "spotify", "weather", "moon", "eyes", "walker")
DATA_DIR = Path(__file__).resolve().parent / ".matrix"


@dataclass(frozen=True)
class ControlSettings:
    scene: str = "clock"
    brightness: int = 80
    gpio_slowdown: int = 2
    theme: str = "light"
    no_numbers: bool = False
    no_ticks: bool = False
    no_progress: bool = False
    spotify_style: str = "cover"
    no_drift_cover: bool = False
    drift_blackness: int = 35
    weather_city: str = DEFAULT_CITY
    weather_demo: str | None = None
    weather_hour: int = 13
    spotify_client_id: str | None = None

    def scene_args(self) -> Namespace:
        return Namespace(**asdict(self))


class SettingsStore:
    def __init__(self, path: Path = DATA_DIR / "control.db") -> None:
        self.path = path

    def load(self) -> ControlSettings:
        if not self.path.exists():
            return ControlSettings()
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = 'control'").fetchone()
        if row is None:
            return ControlSettings()
        saved = json.loads(row[0])
        defaults = asdict(ControlSettings())
        defaults.update({key: value for key, value in saved.items() if key in defaults})
        return ControlSettings(**defaults)

    def save(self, settings: ControlSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO settings (key, value) VALUES ('control', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(asdict(settings)),),
            )
            connection.commit()


class ControlService:
    def __init__(
        self, *, hardware: bool = False, store: SettingsStore | None = None,
        spotify_client_id: str | None = None,
    ):
        self.store = store or SettingsStore()
        self.settings = self.store.load()
        if spotify_client_id:
            from spotify_client import validate_client_id

            self.settings = replace(self.settings, spotify_client_id=validate_client_id(spotify_client_id))
        self.hardware = hardware
        self.applied_gpio_slowdown = self.settings.gpio_slowdown
        self.output = None
        if hardware:
            from hardware_output import MatrixOutput

            # Matrix initialization needs root; the library then drops back to
            # the invoking sudo user before SQLite or Spotify writes occur.
            self.output = MatrixOutput(
                brightness=self.settings.brightness,
                gpio_slowdown=self.settings.gpio_slowdown,
            )
        self.store.save(self.settings)
        self.source = HeadlessScene(self.settings.scene_args())
        try:
            self.source.start()
        except Exception:
            if self.output is not None:
                self.output.close()
            raise
        self.lock = RLock()
        self.stopping = Event()
        self.wake = Event()
        self.thread = Thread(target=self._render_loop, daemon=True, name="matrix-render")
        self.frame = Image.new("RGB", (64, 64), "black")
        self.caption = "Starting…"
        self.last_image = None

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()
        if self.thread.is_alive():
            self.thread.join()
        with self.lock:
            self.source.close()
            if self.output is not None:
                self.output.close()

    def _render_loop(self) -> None:
        while not self.stopping.is_set():
            with self.lock:
                try:
                    frame, caption = self.source.frame()
                    image = frame.tobytes()
                    if image != self.last_image:
                        if self.output is not None:
                            self.output.show(frame)
                        self.frame = frame
                        self.last_image = image
                    self.caption = caption
                except Exception as error:
                    self.caption = f"Render error: {error}"
                interval = self.source.interval
            self.wake.wait(interval)
            self.wake.clear()

    def state(self) -> dict:
        with self.lock:
            result = asdict(self.settings)
            result.pop("spotify_client_id")
            result.update({
                "caption": self.caption,
                "hardware": self.hardware,
                "scenes": SCENES,
                "restart_required": self.settings.gpio_slowdown != self.applied_gpio_slowdown,
            })
            return result

    def frame_png(self) -> bytes:
        with self.lock:
            frame = self.frame.copy()
        buffer = BytesIO()
        frame.save(buffer, format="PNG")
        return buffer.getvalue()

    def select_scene(self, scene: str) -> dict:
        if scene not in SCENES:
            raise ValueError("Unknown scene.")
        with self.lock:
            if scene == self.settings.scene:
                return self.state()
            new_settings = replace(self.settings, scene=scene)
            new_source = HeadlessScene(new_settings.scene_args())
            new_source.start()
            self.source.close()
            self.source = new_source
            self.settings = new_settings
            self.store.save(self.settings)
            self.last_image = None
            self.wake.set()
            return self.state()

    def set_brightness(self, brightness: int) -> dict:
        if not 1 <= brightness <= 100:
            raise ValueError("Brightness must be between 1 and 100.")
        with self.lock:
            self.settings = replace(self.settings, brightness=brightness)
            if self.output is not None:
                self.output.set_brightness(brightness)
            self.store.save(self.settings)
            return self.state()

    def set_gpio_slowdown(self, slowdown: int) -> dict:
        if not 0 <= slowdown <= 10:
            raise ValueError("GPIO slowdown must be between 0 and 10.")
        with self.lock:
            self.settings = replace(self.settings, gpio_slowdown=slowdown)
            self.store.save(self.settings)
            return self.state()
