"""A 64 x 64 LED matrix app with desktop and Raspberry Pi outputs."""

import argparse
from datetime import datetime
import math
from pathlib import Path
import time

from PIL import Image, ImageDraw

from clock_face import draw_clock
from eyes_scene import EyesAnimator, EyesPose, render_eyes_frame
from headless_scene import circle_frame
from kintsugi_scene import KintsugiAnimator, render_kintsugi_frame, stage_name
from shadow_scene import ShadowAnimator, render_shadow_frame
from moon_scene import illumination, moon_phase, nighttime_reference, phase_name, render_moon_frame
from spotify_client import SpotifyClient, SpotifyError, configured_client_id, save_client_id, validate_client_id
from spotify_scene import (
    SPOTIFY_STYLES, SPOTIFY_STYLE_LABELS, SpotifyView, SpotifyWorker,
    render_spotify_frame, waiting_frame,
)
from weather_scene import (
    DEFAULT_CITY, DEMO_WEATHERS, WEATHER_LABELS, WeatherClient, WeatherConditions,
    WeatherError, WeatherWorker, demo_weather, render_weather_frame, weather_kind,
)
from walker_scene import STEP_SECONDS, render_walker_frame


MATRIX_SIZE = (64, 64)
PIXEL_PITCH = 9
BACKGROUND = (8, 10, 14)
UNLIT_LED = (17, 20, 25)


def draw_circle() -> Image.Image:
    """Draw at the physical panel's resolution, independent of the preview."""
    return circle_frame()


def render_leds(frame: Image.Image) -> Image.Image:
    """Enlarge each source pixel into one round LED; never resize the scene."""
    if frame.size != MATRIX_SIZE or frame.mode != "RGB":
        raise ValueError("The matrix expects a 64 x 64 RGB image.")

    # Supersampling only smooths the shape of each preview LED.
    # The scene itself still has exactly 64 x 64 pixels.
    antialias = 3
    pitch = PIXEL_PITCH * antialias
    image = Image.new("RGB", (64 * pitch, 64 * pitch), BACKGROUND)
    draw = ImageDraw.Draw(image)
    inset = 2 * antialias
    for y in range(64):
        for x in range(64):
            color = frame.getpixel((x, y))
            draw.ellipse(
                (
                    x * pitch + inset,
                    y * pitch + inset,
                    (x + 1) * pitch - inset - 1,
                    (y + 1) * pitch - inset - 1,
                ),
                fill=color if color != (0, 0, 0) else UNLIT_LED,
            )
    return image.resize((64 * PIXEL_PITCH, 64 * PIXEL_PITCH), Image.Resampling.LANCZOS)


class MatrixEmulator:
    """Desktop output for the same RGB frames we will send to the real panel."""

    def __init__(
        self, *, scene: str = "clock", fixed_time: datetime | None = None,
        show_numbers: bool = True, show_ticks: bool = True,
        theme: str = "light",
        progress_enabled: bool = True,
        spotify_style: str = "cover",
        drift_cover_enabled: bool = True,
        drift_blackness: int = 35,
        weather_city: str = DEFAULT_CITY,
        weather_demo: str | None = None,
        weather_hour: int = 13,
        kintsugi_theme: str = "indigo", kintsugi_speed: int = 100, kintsugi_density: int = 50,
        shadow_speed: int = 100, shadow_intensity: int = 100,
    ) -> None:
        # Keep desktop imports out of the image-rendering path.
        import tkinter as tk
        from PIL import ImageTk

        self._image_tk = ImageTk
        self._photo = None
        self.spotify_worker = None
        self._spotify_view = None
        self.weather_worker = None
        self._weather_conditions: WeatherConditions | None = None
        self._weather_error = ""
        self.eyes_animator = EyesAnimator(time.monotonic()) if scene == "eyes" else None
        self.walk_started = time.monotonic()
        self.kintsugi = KintsugiAnimator(time.monotonic()) if scene == "kintsugi" else None
        self.kintsugi_theme = kintsugi_theme
        self.kintsugi_speed = kintsugi_speed
        self.kintsugi_density = kintsugi_density
        self.shadow = ShadowAnimator(time.monotonic()) if scene == "shadow" else None
        self.shadow_speed = shadow_speed
        self.shadow_intensity = shadow_intensity
        self.weather_city = weather_city
        self.weather_demo = weather_demo
        self.weather_demo_hour = weather_hour
        self.spotify_url = ""
        self.scene = scene
        self.fixed_time = fixed_time
        self.show_numbers = show_numbers
        self.show_ticks = show_ticks
        self.theme = theme
        self.progress_enabled = progress_enabled
        self.spotify_style = spotify_style
        self.drift_cover_enabled = drift_cover_enabled
        self.drift_blackness = max(0, min(100, drift_blackness))
        self.hand_length_percent = 100
        self.hand_width_percent = 100
        self._last_frame_key = None
        self.root = tk.Tk()
        self.root.title("LED Matrix · 64 × 64")
        self.root.configure(background="#080a0e")
        self.root.resizable(False, False)
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("n", lambda _event: self.toggle("show_numbers"))
        self.root.bind("t", lambda _event: self.toggle("show_ticks"))
        self.root.bind("<Right>", lambda _event: self.adjust_hands(length=5))
        self.root.bind("<Left>", lambda _event: self.adjust_hands(length=-5))
        self.root.bind("<Up>", lambda _event: self.adjust_hands(width=10))
        self.root.bind("<Down>", lambda _event: self.adjust_hands(width=-10))
        self.root.bind("r", lambda _event: self.reset_hands())
        self.root.bind("l", lambda _event: self.toggle_theme())
        self.root.bind("p", lambda _event: self.toggle_progress())
        self.root.bind("v", lambda _event: self.cycle_spotify_style())
        self.root.bind("h", lambda _event: self.toggle_drift_cover())
        self.root.bind("-", lambda _event: self.adjust_kintsugi(speed=-25) if self.scene == "kintsugi" else self.adjust_drift_blackness(5))
        self.root.bind("=", lambda _event: self.adjust_kintsugi(speed=25) if self.scene == "kintsugi" else self.adjust_drift_blackness(-5))
        self.root.bind("[", lambda _event: self.adjust_kintsugi(density=-10))
        self.root.bind("]", lambda _event: self.adjust_kintsugi(density=10))
        self.root.bind("d", lambda _event: self.cycle_on_d())

        self.display = tk.Label(self.root, background="#080a0e", borderwidth=0)
        self.display.pack(padx=24, pady=(24, 16))
        self.caption = tk.Label(
            self.root,
            background="#080a0e",
            foreground="#89909c",
            font=("Consolas", 10),
            wraplength=64 * PIXEL_PITCH,
        )
        self.caption.pack(pady=(0, 24))
        if self.scene == "spotify":
            actions = tk.Frame(self.root, background="#080a0e")
            actions.pack(pady=(0, 24))
            self.connect_button = tk.Button(actions, text="Connect Spotify", command=self.connect_spotify)
            self.connect_button.pack(side="left", padx=8)
            self.track_button = tk.Button(actions, text="Open in Spotify", command=self.open_track, state="disabled")
            self.track_button.pack(side="left", padx=8)
            self.progress_button = tk.Button(actions, text="Progress: ON", command=self.toggle_progress)
            self.progress_button.pack(side="left", padx=8)
            self.style_button = tk.Button(actions, text="Style: COVER", command=self.cycle_spotify_style)
            self.style_button.pack(side="left", padx=8)
            self.drift_cover_button = tk.Button(
                actions, text="Cover: ON", command=self.toggle_drift_cover,
            )
            self.drift_cover_button.pack(side="left", padx=8)
            self.drift_settings = tk.Frame(self.root, background="#080a0e")
            self.drift_settings.pack(pady=(0, 16))
            self.drift_blackness_scale = tk.Scale(
                self.drift_settings, from_=0, to=100, resolution=5,
                orient="horizontal", length=220, label="Black amount",
                showvalue=True, command=self.set_drift_blackness,
                background="#080a0e", foreground="#89909c",
                troughcolor="#202632", highlightthickness=0,
            )
            self.drift_blackness_scale.set(self.drift_blackness)
            self.drift_blackness_scale.pack()
            self.apply_spotify_view(SpotifyView(
                waiting_frame(), "Connect Spotify to display your album covers.", needs_login=True
            ))
            try:
                client_id = configured_client_id()
                if client_id:
                    self.start_spotify(client_id)
            except SpotifyError as error:
                self.caption.configure(text=str(error))
        elif self.scene == "weather":
            actions = tk.Frame(self.root, background="#080a0e")
            actions.pack(pady=(0, 20))
            if self.weather_demo:
                self.weather_button = tk.Button(actions, text="Next weather", command=self.cycle_weather_demo)
                self._weather_conditions = demo_weather(self.weather_demo)
                self.root.bind("<Left>", lambda _event: self.adjust_weather_demo_time(-1))
                self.root.bind("<Right>", lambda _event: self.adjust_weather_demo_time(1))
            else:
                self.weather_button = tk.Button(actions, text="Change city", command=self.change_weather_city)
                self.weather_worker = WeatherWorker(weather_city)
                self.weather_worker.start()
            self.weather_button.pack()

    def start_spotify(self, client_id: str) -> None:
        self.spotify_worker = SpotifyWorker(SpotifyClient(client_id))
        self.spotify_worker.start()

    def connect_spotify(self) -> None:
        from tkinter import messagebox, simpledialog

        try:
            if self.spotify_worker is None:
                client_id = simpledialog.askstring(
                    "Connect Spotify",
                    "Paste your Spotify app's Client ID (not the secret).\n"
                    "First save this Redirect URI in its settings:\n"
                    "http://127.0.0.1:8888/callback",
                    parent=self.root,
                )
                if client_id is None:
                    return
                client_id = validate_client_id(client_id)
                save_client_id(client_id)
                self.start_spotify(client_id)
            self.apply_spotify_view(SpotifyView(
                waiting_frame(), "Opening Spotify sign-in…", connecting=True
            ))
            self.spotify_worker.connect()
        except (SpotifyError, OSError) as error:
            messagebox.showerror("Could not connect Spotify", str(error), parent=self.root)

    def apply_spotify_view(self, view: SpotifyView) -> None:
        # Retain every timing sample, even when the album and caption match.
        self._spotify_view = view
        self.refresh_spotify_display()

    def toggle_progress(self) -> None:
        if self.scene == "spotify":
            self.progress_enabled = not getattr(self, "progress_enabled", True)
            self.refresh_spotify_display()

    def cycle_spotify_style(self) -> None:
        if self.scene != "spotify":
            return
        current = getattr(self, "spotify_style", "cover")
        try:
            index = SPOTIFY_STYLES.index(current)
        except ValueError:
            index = 0
        self.spotify_style = SPOTIFY_STYLES[(index + 1) % len(SPOTIFY_STYLES)]
        self._last_frame_key = None
        self.refresh_spotify_display()

    def toggle_drift_cover(self) -> None:
        if self.scene == "spotify":
            self.drift_cover_enabled = not getattr(self, "drift_cover_enabled", True)
            self._last_frame_key = None
            self.refresh_spotify_display()

    def set_drift_blackness(self, value: str | int) -> None:
        if self.scene == "spotify":
            self.drift_blackness = max(0, min(100, int(round(float(value)))))
            self._last_frame_key = None
            self.refresh_spotify_display()

    def adjust_drift_blackness(self, amount: int) -> None:
        if self.scene == "spotify":
            self.set_drift_blackness(getattr(self, "drift_blackness", 35) + amount)

    def refresh_spotify_display(self) -> None:
        view = self._spotify_view
        if view is None:
            return
        progress_enabled = getattr(self, "progress_enabled", True)
        style = getattr(self, "spotify_style", "cover")
        drift_cover_enabled = getattr(self, "drift_cover_enabled", True)
        drift_blackness = getattr(self, "drift_blackness", 35)
        frame = render_spotify_frame(
            view, show_progress=progress_enabled, style=style,
            rotation_degrees=time.monotonic() * 36,
            show_drift_cover=drift_cover_enabled,
            drift_blackness=drift_blackness / 100,
        )
        key = (
            frame.tobytes(), view.caption, view.spotify_url, view.needs_login,
            view.connecting, progress_enabled, style, drift_cover_enabled, drift_blackness,
        )
        if key == self._last_frame_key:
            return
        self.show(frame)
        self.caption.configure(text=view.caption)
        self.spotify_url = view.spotify_url
        self.connect_button.configure(
            state="normal" if view.needs_login else "disabled",
            text="Connecting…" if view.connecting else ("Connect Spotify" if view.needs_login else "Connected"),
        )
        self.track_button.configure(state="normal" if self.spotify_url else "disabled")
        if hasattr(self, "progress_button"):
            self.progress_button.configure(text=f"Progress: {'ON' if progress_enabled else 'OFF'}")
        if hasattr(self, "style_button"):
            self.style_button.configure(text=f"Style: {SPOTIFY_STYLE_LABELS.get(style, style.upper())}")
        if hasattr(self, "drift_cover_button"):
            self.drift_cover_button.configure(
                text=f"Cover: {'ON' if drift_cover_enabled else 'OFF'}",
                state="normal" if style == "colour-drift" else "disabled",
            )
        if hasattr(self, "drift_blackness_scale"):
            self.drift_blackness_scale.configure(
                state="normal" if style == "colour-drift" else "disabled",
            )
        controls = (
            f"V: {SPOTIFY_STYLE_LABELS.get(style, style.upper())}"
            f" · P: {'ON' if progress_enabled else 'OFF'}"
        )
        if style == "colour-drift":
            controls += (
                f" · H: COVER {'ON' if drift_cover_enabled else 'OFF'}"
                f" · -/=: BLACK {drift_blackness}%"
            )
        self.caption.configure(
            text=f"{view.caption}\n{controls}"
        )
        self._last_frame_key = key

    def open_track(self) -> None:
        from urllib.parse import urlsplit
        import webbrowser

        parsed = urlsplit(self.spotify_url)
        if parsed.scheme == "https" and parsed.hostname == "open.spotify.com":
            webbrowser.open(self.spotify_url)

    def close(self) -> None:
        if self.spotify_worker is not None:
            self.spotify_worker.close()
        if self.weather_worker is not None:
            self.weather_worker.close()
        self.root.destroy()

    def change_weather_city(self) -> None:
        if self.scene != "weather" or self.weather_worker is None:
            return
        from tkinter import simpledialog

        city = simpledialog.askstring(
            "Weather glass", "City or city, country:",
            initialvalue=self.weather_city, parent=self.root,
        )
        if city and len(city.strip()) >= 2:
            self.weather_city = city.strip()
            self._weather_conditions = None
            self._weather_error = ""
            self.weather_worker.set_city(self.weather_city)
            self._last_frame_key = None
            self.refresh_weather_display()

    def cycle_weather_demo(self) -> None:
        if self.scene != "weather" or self.weather_demo is None:
            return
        index = DEMO_WEATHERS.index(self.weather_demo)
        self.weather_demo = DEMO_WEATHERS[(index + 1) % len(DEMO_WEATHERS)]
        self._weather_conditions = demo_weather(self.weather_demo)
        self._last_frame_key = None
        self.refresh_weather_display()

    def cycle_on_d(self) -> None:
        if self.scene in ("eyes", "walker", "kintsugi", "shadow"):
            self.cycle_animation()
        else:
            self.cycle_weather_demo()

    def cycle_animation(self) -> None:
        if self.scene == "eyes":
            self.scene = "walker"
            self.walk_started = time.monotonic()
        elif self.scene == "walker":
            self.scene = "kintsugi"
            self.kintsugi = KintsugiAnimator(time.monotonic())
        elif self.scene == "kintsugi":
            self.scene = "shadow"
            self.shadow = ShadowAnimator(time.monotonic())
        elif self.scene == "shadow":
            self.scene = "eyes"
            self.eyes_animator = EyesAnimator(time.monotonic())
        else:
            return
        self._last_frame_key = None

    def adjust_weather_demo_time(self, hours: int) -> None:
        if self.scene != "weather" or self.weather_demo is None:
            return
        self.weather_demo_hour = (self.weather_demo_hour + hours) % 24
        self.refresh_weather_display()

    def refresh_weather_display(self) -> None:
        weather = self._weather_conditions
        if weather is None:
            frame = render_weather_frame(demo_weather("fog"), phase=5, show_temperature=False)
            caption = f"Loading current weather for {self.weather_city}…"
        else:
            frame = render_weather_frame(
                weather, phase=time.monotonic(),
                local_hour=self.weather_demo_hour if self.weather_demo else None,
            )
            label = WEATHER_LABELS[weather_kind(weather.code)]
            caption = f"{weather.city} · {label} · {round(weather.temperature_c)}°C"
            if self.weather_demo:
                caption += f"\nDEMO {self.weather_demo_hour:02d}:00 · D: weather · ←/→: time"
            else:
                caption += "\nWeather: Open-Meteo"
                if weather.observed_at:
                    caption += f" · {weather.observed_at[-5:]}"
        if self._weather_error:
            caption += f"\n{self._weather_error}"
        key = (frame.tobytes(), caption)
        if key != self._last_frame_key:
            self.show(frame)
            self.caption.configure(text=caption)
            self._last_frame_key = key

    def toggle(self, setting: str) -> None:
        if self.scene == "clock":
            setattr(self, setting, not getattr(self, setting))
            self._last_frame_key = None

    def adjust_hands(self, *, length: int = 0, width: int = 0) -> None:
        if self.scene == "clock":
            # Keep even the longest, boldest hands inside the physical panel.
            self.hand_length_percent = min(130, max(60, self.hand_length_percent + length))
            self.hand_width_percent = min(250, max(50, self.hand_width_percent + width))
        elif self.scene == "shadow":
            self.shadow_intensity = min(100, max(25, self.shadow_intensity + length))
            self.shadow_speed = min(200, max(25, self.shadow_speed + width))

    def toggle_theme(self) -> None:
        if self.scene == "clock":
            self.theme = "dark" if self.theme == "light" else "light"
        elif self.scene == "kintsugi":
            self.kintsugi_theme = "ivory" if self.kintsugi_theme == "indigo" else "indigo"

    def adjust_kintsugi(self, *, speed: int = 0, density: int = 0) -> None:
        if self.scene == "kintsugi":
            self.kintsugi_speed = min(300, max(25, self.kintsugi_speed + speed))
            self.kintsugi_density = min(100, max(20, self.kintsugi_density + density))

    def reset_hands(self) -> None:
        if self.scene == "clock":
            self.hand_length_percent = 100
            self.hand_width_percent = 100
        elif self.scene == "kintsugi":
            self.kintsugi = KintsugiAnimator(time.monotonic())
        elif self.scene == "shadow":
            self.shadow = ShadowAnimator(time.monotonic())

    def update(self) -> None:
        if self.scene in ("eyes", "walker", "kintsugi", "shadow"):
            now = time.monotonic()
            if self.scene == "eyes":
                frame = render_eyes_frame(self.eyes_animator.pose(now))
                caption = "EYES · D: WALKER"
            elif self.scene == "walker":
                phase = 2 * math.pi * (now - self.walk_started) / STEP_SECONDS
                frame = render_walker_frame(phase)
                caption = "WALKER · D: KINTSUGI"
            elif self.scene == "kintsugi":
                frame = self.kintsugi.frame(
                    now, theme=self.kintsugi_theme, speed=self.kintsugi_speed, density=self.kintsugi_density,
                )
                caption = (
                    f"KINTSUGI · {stage_name(self.kintsugi.phase)} · L: {self.kintsugi_theme.upper()}"
                    f"\n-/=: PACE {self.kintsugi_speed}% · [/]: DENSITY {self.kintsugi_density}%"
                    "\nR: NEW TILE · D: SHADOW"
                )
            else:
                frame = self.shadow.frame(now, speed=self.shadow_speed, intensity=self.shadow_intensity)
                caption = (
                    "IMPOSSIBLE SHADOW"
                    f"\n↑/↓: PACE {self.shadow_speed}% · ←/→: UNEASE {self.shadow_intensity}%"
                    "\nR: RESTART · D: EYES"
                )
            key = (frame.tobytes(), caption)
            if key != self._last_frame_key:
                self.show(frame)
                self.caption.configure(text=caption)
                self._last_frame_key = key
            self.root.after(50, self.update)
            return
        if self.scene == "moon":
            night = nighttime_reference()
            if night != self._last_frame_key:
                phase = moon_phase(night)
                self.show(render_moon_frame(phase))
                self.caption.configure(
                    text=f"{phase_name(phase)} · {round(illumination(phase) * 100)}% illuminated"
                    f"\nFor the night of {night.astimezone():%d %b %Y}"
                )
                self._last_frame_key = night
            self.root.after(60_000, self.update)
            return
        if self.scene == "weather":
            if self.weather_worker is not None:
                update = self.weather_worker.latest()
                if update is not None:
                    if update.conditions is not None:
                        self._weather_conditions = update.conditions
                        self._weather_error = ""
                    else:
                        self._weather_error = update.error
            self.refresh_weather_display()
            self.root.after(100, self.update)
            return
        if self.scene == "spotify":
            view = None
            if self.spotify_worker is not None:
                view = self.spotify_worker.latest()
            if view is not None:
                self.apply_spotify_view(view)
            else:
                self.refresh_spotify_display()
            self.root.after(100, self.update)
            return
        moment = self.fixed_time or datetime.now()
        key = (
            moment.replace(microsecond=0), self.show_numbers, self.show_ticks,
            self.hand_length_percent, self.hand_width_percent,
            self.theme,
        )
        if self.scene == "circle":
            self.show(draw_circle())
            self.caption.configure(text="64 × 64   /   CIRCLE")
            return
        if key != self._last_frame_key:
            self.show(draw_clock(
                moment, show_numbers=self.show_numbers, show_ticks=self.show_ticks,
                hand_length_scale=self.hand_length_percent / 100,
                hand_width_scale=self.hand_width_percent / 100,
                theme=self.theme,
            ))
            self.caption.configure(
                text=f"64 × 64   /   N: NUMBERS {'ON' if self.show_numbers else 'OFF'}"
                f"   /   T: TICKS {'ON' if self.show_ticks else 'OFF'}"
                f"\n← / →: LENGTH {self.hand_length_percent}%"
                f"   /   ↓ / ↑: WIDTH {self.hand_width_percent}%"
                f"\nL: THEME {self.theme.upper()}   /   R: RESET HANDS"
            )
            self._last_frame_key = key
        self.root.after(100, self.update)

    def show(self, frame: Image.Image) -> None:
        self._photo = self._image_tk.PhotoImage(render_leds(frame), master=self.root)
        self.display.configure(image=self._photo)

    def run(self) -> None:
        self.update()
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", choices=("emulator", "matrix"), default="emulator",
        help="Show scenes in the desktop emulator or on the physical HUB75 matrix.",
    )
    parser.add_argument(
        "--snapshot", type=Path, metavar="PNG", help="Save the LED preview without opening a window."
    )
    parser.add_argument("--scene", choices=("clock", "circle", "spotify", "weather", "moon", "eyes", "walker", "kintsugi", "shadow"), default="clock")
    parser.add_argument("--shadow-speed", type=int, default=100, help="Shadow animation pace, 25–200 percent.")
    parser.add_argument("--shadow-intensity", type=int, default=100, help="Shadow distortion and approach, 25–100 percent.")
    parser.add_argument("--kintsugi-theme", choices=("indigo", "ivory"), default="indigo")
    parser.add_argument("--kintsugi-speed", type=int, default=100, help="Kintsugi pace, 25–300 percent.")
    parser.add_argument("--kintsugi-density", type=int, default=50, help="Kintsugi crack density, 20–100 percent.")
    parser.add_argument("--time", metavar="HH:MM:SS", help="Freeze the clock at a chosen time.")
    parser.add_argument("--no-numbers", action="store_true", help="Hide the clock numerals.")
    parser.add_argument("--no-ticks", action="store_true", help="Hide the clock tick marks.")
    parser.add_argument(
        "--theme", choices=("light", "dark"), default="light", help="Clock colors (default: light)."
    )
    parser.add_argument("--no-progress", action="store_true", help="Hide the Spotify progress frame.")
    parser.add_argument(
        "--no-drift-cover", action="store_true",
        help="Hide the album cover in the soft colour drift style.",
    )
    parser.add_argument(
        "--drift-blackness", type=int, default=35, metavar="PERCENT",
        help="Blackness of the colour drift background, from 0 to 100 (default: 35).",
    )
    parser.add_argument(
        "--spotify-style", choices=SPOTIFY_STYLES, default="cover",
        help="Spotify display style: cover, vinyl-spin, vinyl-still, or colour-drift.",
    )
    parser.add_argument(
        "--spotify-client-id", metavar="ID",
        help="Spotify app Client ID for headless sign-in on the Pi.",
    )
    parser.add_argument(
        "--brightness", type=int, default=80, metavar="PERCENT",
        help="Physical matrix brightness, from 1 to 100 (default: 80).",
    )
    parser.add_argument(
        "--gpio-slowdown", type=int, default=2, metavar="N",
        help="Physical matrix GPIO slowdown, from 0 to 10 (default: 2).",
    )
    parser.add_argument(
        "--weather-city", default=DEFAULT_CITY, metavar="CITY",
        help="City for live weather (default: Fribourg, Switzerland).",
    )
    parser.add_argument(
        "--weather-demo", choices=DEMO_WEATHERS,
        help="Preview a weather condition without network access.",
    )
    parser.add_argument(
        "--weather-hour", type=int, default=13, metavar="HOUR",
        help="Starting demo hour from 0 to 23 (default: 13); Left/Right changes it in the window.",
    )
    args = parser.parse_args()
    if not 25 <= args.shadow_speed <= 200:
        parser.error("--shadow-speed must be between 25 and 200.")
    if not 25 <= args.shadow_intensity <= 100:
        parser.error("--shadow-intensity must be between 25 and 100.")
    if not 25 <= args.kintsugi_speed <= 300:
        parser.error("--kintsugi-speed must be between 25 and 300.")
    if not 20 <= args.kintsugi_density <= 100:
        parser.error("--kintsugi-density must be between 20 and 100.")
    if not 0 <= args.drift_blackness <= 100:
        parser.error("--drift-blackness must be between 0 and 100.")
    if not 0 <= args.weather_hour <= 23:
        parser.error("--weather-hour must be between 0 and 23.")
    if not 1 <= args.brightness <= 100:
        parser.error("--brightness must be between 1 and 100.")
    if not 0 <= args.gpio_slowdown <= 10:
        parser.error("--gpio-slowdown must be between 0 and 10.")
    if args.snapshot and args.output == "matrix":
        parser.error("--snapshot saves an emulator preview; omit --output matrix.")
    fixed_time = None
    if args.time:
        try:
            fixed_time = datetime.strptime(args.time, "%H:%M:%S")
        except ValueError:
            parser.error("--time must be a valid 24-hour time in HH:MM:SS format.")

    if args.snapshot:
        if args.scene == "weather":
            try:
                weather = demo_weather(args.weather_demo) if args.weather_demo else WeatherClient().current(args.weather_city)
                frame = render_weather_frame(
                    weather, phase=1,
                    local_hour=args.weather_hour if args.weather_demo else None,
                )
            except WeatherError as error:
                parser.exit(1, str(error) + "\n")
        elif args.scene == "spotify":
            try:
                client_id = configured_client_id()
                if not client_id:
                    raise SpotifyError("Run with --scene spotify and click Connect Spotify first.")
                view = SpotifyWorker(SpotifyClient(client_id)).poll_once()
                frame = render_spotify_frame(
                    view, show_progress=not args.no_progress, style=args.spotify_style,
                    show_drift_cover=not args.no_drift_cover,
                    drift_blackness=args.drift_blackness / 100,
                )
            except (SpotifyError, OSError) as error:
                parser.exit(1, str(error) + "\n")
        elif args.scene == "moon":
            frame = render_moon_frame(moon_phase(nighttime_reference()))
        elif args.scene == "eyes":
            frame = render_eyes_frame(EyesPose())
        elif args.scene == "walker":
            frame = render_walker_frame(math.pi / 2)
        elif args.scene == "kintsugi":
            frame = render_kintsugi_frame(.64, theme=args.kintsugi_theme, density=args.kintsugi_density)
        elif args.scene == "shadow":
            frame = render_shadow_frame(.76, intensity=args.shadow_intensity)
        else:
            frame = draw_circle() if args.scene == "circle" else draw_clock(
                fixed_time, show_numbers=not args.no_numbers, show_ticks=not args.no_ticks,
                theme=args.theme,
            )
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        render_leds(frame).save(args.snapshot, format="PNG")
        return

    if args.output == "matrix":
        from headless_scene import run_matrix

        try:
            run_matrix(args, fixed_time)
        except (RuntimeError, SpotifyError) as error:
            parser.exit(1, f"{error}\n")
        return

    emulator = MatrixEmulator(
        scene=args.scene, fixed_time=fixed_time,
        show_numbers=not args.no_numbers, show_ticks=not args.no_ticks,
        theme=args.theme,
        progress_enabled=not args.no_progress,
        spotify_style=args.spotify_style,
        drift_cover_enabled=not args.no_drift_cover,
        drift_blackness=args.drift_blackness,
        weather_city=args.weather_city,
        weather_demo=args.weather_demo,
        weather_hour=args.weather_hour,
        kintsugi_theme=args.kintsugi_theme, kintsugi_speed=args.kintsugi_speed,
        kintsugi_density=args.kintsugi_density,
        shadow_speed=args.shadow_speed, shadow_intensity=args.shadow_intensity,
    )
    emulator.run()


if __name__ == "__main__":
    main()
