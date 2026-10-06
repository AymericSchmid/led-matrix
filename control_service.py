"""Persistent state and one render loop shared by matrix and phone preview."""

from argparse import Namespace
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from io import BytesIO
from pathlib import Path
import json
import secrets
import sqlite3
from threading import Event, RLock, Thread
import time

from PIL import Image

from headless_scene import HeadlessScene
from spotify_client import (
    DATA_DIR as SPOTIFY_DATA_DIR, REDIRECT_URI, SpotifyClient, SpotifyError, authorization_url,
    bridge_state, configured_client_id, configured_env_client_id, phone_callback_result,
    phone_redirect_uri, pkce_pair, validate_client_id,
)
from spotify_scene import SPOTIFY_STYLES
from weather_scene import DEFAULT_CITY, DEMO_WEATHERS


SCENES = ("clock", "circle", "spotify", "weather", "moon", "eyes", "walker", "kintsugi")
SCENE_OPTIONS = {
    "clock": frozenset(("theme", "no_numbers", "no_ticks", "hand_length_percent", "hand_width_percent")),
    "spotify": frozenset(("spotify_style", "no_progress", "no_drift_cover", "drift_blackness")),
    "weather": frozenset(("weather_city", "weather_demo", "weather_hour")),
    "eyes": frozenset(("eyes_movement_rate", "eyes_blink_rate")),
    "kintsugi": frozenset(("kintsugi_theme", "kintsugi_speed", "kintsugi_density")),
}
DATA_DIR = Path(__file__).resolve().parent / ".matrix"


@dataclass(frozen=True)
class ControlSettings:
    scene: str = "clock"
    brightness: int = 80
    gpio_slowdown: int = 2
    theme: str = "light"
    no_numbers: bool = False
    no_ticks: bool = False
    hand_length_percent: int = 100
    hand_width_percent: int = 100
    no_progress: bool = False
    spotify_style: str = "cover"
    no_drift_cover: bool = False
    drift_blackness: int = 35
    weather_city: str = DEFAULT_CITY
    weather_demo: str | None = None
    weather_hour: int = 13
    eyes_movement_rate: int = 100
    eyes_blink_rate: int = 100
    kintsugi_theme: str = "indigo"
    kintsugi_speed: int = 100
    kintsugi_density: int = 50
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


@dataclass(frozen=True)
class PendingSpotifyLogin:
    client_id: str
    state: str
    verifier: str
    url: str
    redirect_uri: str
    expires_at: float


class ControlService:
    def __init__(
        self, *, hardware: bool = False, store: SettingsStore | None = None,
        spotify_client_id: str | None = None, spotify_data_dir: Path = SPOTIFY_DATA_DIR,
    ):
        self.store = store or SettingsStore()
        self.settings = self.store.load()
        fixed_client_id = spotify_client_id or configured_env_client_id()
        self.spotify_client_id_locked = bool(fixed_client_id)
        self.spotify_redirect_uri = phone_redirect_uri()
        if fixed_client_id:
            self.settings = replace(self.settings, spotify_client_id=validate_client_id(fixed_client_id))
        self.spotify_data_dir = spotify_data_dir
        self.spotify_login: PendingSpotifyLogin | None = None
        self.spotify_login_busy = False
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
        self.source = self._new_source(self.settings)
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

    def _new_source(self, settings: ControlSettings) -> HeadlessScene:
        return HeadlessScene(
            settings.scene_args(), auto_spotify_login=False,
            spotify_data_dir=self.spotify_data_dir,
        )

    def _replace_source(self, settings: ControlSettings) -> None:
        new_source = self._new_source(settings)
        new_source.start()
        self.source.close()
        self.source = new_source
        self.last_image = None
        self.wake.set()

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
            self._replace_source(new_settings)
            self.settings = new_settings
            self.store.save(self.settings)
            return self.state()

    def set_scene_options(self, scene: str, changes: dict) -> dict:
        if scene not in SCENES:
            raise ValueError("Unknown scene.")
        allowed = SCENE_OPTIONS.get(scene, frozenset())
        if not changes or not changes.keys() <= allowed:
            raise ValueError("Unknown or missing setting for this scene.")

        for key, value in changes.items():
            if key in ("no_numbers", "no_ticks", "no_progress", "no_drift_cover"):
                valid = type(value) is bool
            elif key in (
                "hand_length_percent", "hand_width_percent", "drift_blackness", "weather_hour",
                "eyes_movement_rate", "eyes_blink_rate",
                "kintsugi_speed", "kintsugi_density",
            ):
                limits = {
                    "hand_length_percent": (60, 130), "hand_width_percent": (50, 250),
                    "drift_blackness": (0, 100), "weather_hour": (0, 23),
                    "eyes_movement_rate": (25, 300), "eyes_blink_rate": (25, 300),
                    "kintsugi_speed": (25, 300), "kintsugi_density": (20, 100),
                }
                lower, upper = limits[key]
                valid = type(value) is int and lower <= value <= upper
            elif key == "theme":
                valid = value in ("light", "dark")
            elif key == "kintsugi_theme":
                valid = value in ("indigo", "ivory")
            elif key == "spotify_style":
                valid = value in SPOTIFY_STYLES
            elif key == "weather_demo":
                valid = value is None or value in DEMO_WEATHERS
            else:
                valid = isinstance(value, str) and 1 <= len(value.strip()) <= 100
                if valid:
                    changes[key] = value.strip()
            if not valid:
                raise ValueError(f"Invalid value for {key.replace('_', ' ')}.")

        with self.lock:
            updated = replace(self.settings, **changes)
            if updated == self.settings:
                return self.state()
            if scene == self.settings.scene:
                if scene == "weather" and any(
                    getattr(updated, key) != getattr(self.settings, key)
                    for key in ("weather_city", "weather_demo")
                ):
                    self._replace_source(updated)
                else:
                    self.source.args = updated.scene_args()
                    self.wake.set()
            self.settings = updated
            self.store.save(updated)
            return self.state()

    def _spotify_client_id(self) -> str:
        return self.settings.spotify_client_id or configured_client_id()

    def spotify_status(self) -> dict:
        with self.lock:
            client_id = self._spotify_client_id()
            token = SpotifyClient(client_id, self.spotify_data_dir).token if client_id else {}
            pending = self.spotify_login is not None and time.monotonic() < self.spotify_login.expires_at
            return {
                "client_id": client_id, "connected": bool(token), "pending": pending,
                "login_url": self.spotify_login.url if pending else None,
                "client_id_locked": self.spotify_client_id_locked,
                "redirect_uri": self.spotify_redirect_uri,
            }

    def start_spotify_login(self, client_id: str | None = None, return_origin: str | None = None) -> dict:
        with self.lock:
            if self.spotify_login_busy:
                raise SpotifyError("A Spotify sign-in is already finishing. Please wait a moment.")
            if self.spotify_client_id_locked and client_id and client_id != self.settings.spotify_client_id:
                raise SpotifyError("This Client ID is set on the Pi. Change its .env or launch option, then restart the controller.")
            chosen = validate_client_id(client_id or self._spotify_client_id())
            if chosen != self.settings.spotify_client_id:
                new_settings = replace(self.settings, spotify_client_id=chosen)
                if self.settings.scene == "spotify":
                    self._replace_source(new_settings)
                self.settings = new_settings
                self.store.save(self.settings)
            verifier, challenge = pkce_pair()
            nonce = secrets.token_urlsafe(32)
            state = bridge_state(nonce, return_origin) if return_origin else nonce
            redirect_uri = self.spotify_redirect_uri if return_origin else REDIRECT_URI
            url = authorization_url(chosen, challenge, state, redirect_uri)
            self.spotify_login = PendingSpotifyLogin(
                chosen, state, verifier, url, redirect_uri, time.monotonic() + 300,
            )
            return {"url": url, "client_id": chosen}

    def finish_spotify_login(self, callback_url: str) -> dict:
        with self.lock:
            pending = self.spotify_login
            if pending is None or time.monotonic() >= pending.expires_at:
                self.spotify_login = None
                raise SpotifyError("This sign-in has expired. Start a new Spotify sign-in.")
            if pending.redirect_uri != REDIRECT_URI:
                raise SpotifyError("This sign-in returns automatically to the Pi. Open Spotify again from the controller.")
            code, error = phone_callback_result(callback_url, pending.state)
            return self._finish_spotify_login(code, pending.state, error)

    def finish_spotify_redirect(self, code: str, state: str, error: str = "") -> dict:
        with self.lock:
            pending = self.spotify_login
            if pending is None or pending.redirect_uri == REDIRECT_URI:
                raise SpotifyError("No phone sign-in is waiting. Start again from the controller.")
            return self._finish_spotify_login(code, state, error)

    def _finish_spotify_login(self, code: str, state: str, error: str) -> dict:
        with self.lock:
            pending = self.spotify_login
            if pending is None or time.monotonic() >= pending.expires_at:
                self.spotify_login = None
                raise SpotifyError("This sign-in has expired. Start a new Spotify sign-in.")
            if not secrets.compare_digest(state, pending.state):
                raise SpotifyError("That Spotify return address belongs to another sign-in attempt.")
            if self.spotify_login_busy:
                raise SpotifyError("A Spotify sign-in is already finishing. Please wait a moment.")
            if error:
                self.spotify_login = None
                raise SpotifyError("Spotify sign-in was cancelled. You can try again.")
            if not code:
                raise SpotifyError("That Spotify return address has no authorization code.")
            self.spotify_login_busy = True

        try:
            SpotifyClient(pending.client_id, self.spotify_data_dir).exchange_code(
                code, pending.verifier, pending.redirect_uri,
            )
        except Exception:
            with self.lock:
                self.spotify_login = None
                self.spotify_login_busy = False
            raise
        with self.lock:
            self.spotify_login = None
            self.spotify_login_busy = False
            if self.settings.scene == "spotify":
                self._replace_source(self.settings)
            return self.spotify_status()

    def disconnect_spotify(self) -> dict:
        with self.lock:
            if self.spotify_login_busy:
                raise SpotifyError("A Spotify sign-in is finishing. Please wait a moment.")
            self.spotify_login = None
            (self.spotify_data_dir / "tokens.json").unlink(missing_ok=True)
            if self.settings.scene == "spotify":
                self._replace_source(self.settings)
            return self.spotify_status()

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
