"""Scene timing and data polling for the physical matrix, without Tkinter."""

from datetime import datetime
import math
import time

from PIL import Image, ImageDraw

from clock_face import draw_clock
from eyes_scene import EyesAnimator, render_eyes_frame
from kintsugi_scene import KintsugiAnimator, stage_name
from moon_scene import nighttime_reference, moon_phase, render_moon_frame
from spotify_client import DATA_DIR as SPOTIFY_DATA_DIR, SpotifyClient, SpotifyError, configured_client_id, validate_client_id
from spotify_scene import SpotifyView, SpotifyWorker, render_spotify_frame, waiting_frame
from weather_scene import WeatherWorker, demo_weather, render_weather_frame
from walker_scene import STEP_SECONDS, render_walker_frame


def circle_frame() -> Image.Image:
    frame = Image.new("RGB", (64, 64), "black")
    ImageDraw.Draw(frame).ellipse((11, 11, 52, 52), outline=(255, 181, 102), width=2)
    return frame


class HeadlessScene:
    def __init__(
        self, args, fixed_time: datetime | None = None, *,
        auto_spotify_login: bool = True, spotify_data_dir=None,
    ):
        self.args = args
        self.fixed_time = fixed_time
        self.auto_spotify_login = auto_spotify_login
        self.spotify_data_dir = spotify_data_dir or SPOTIFY_DATA_DIR
        self.started = time.monotonic()
        self.kintsugi = KintsugiAnimator(self.started) if args.scene == "kintsugi" else None
        self.eyes = EyesAnimator(
            self.started,
            movement_rate=getattr(args, "eyes_movement_rate", 100) / 100,
            blink_rate=getattr(args, "eyes_blink_rate", 100) / 100,
        ) if args.scene == "eyes" else None
        self.spotify_worker = None
        self.spotify_view = SpotifyView(waiting_frame(), "Waiting for Spotify…")
        self.weather_worker = None
        self.weather = demo_weather(args.weather_demo) if args.scene == "weather" and args.weather_demo else None
        self.last_caption = None
        self.interval = {
            "eyes": .05, "walker": .05, "spotify": .1, "weather": .1,
            "clock": .1, "moon": 60, "circle": 1,
            "kintsugi": .1,
        }[args.scene]

    def start(self) -> None:
        if self.args.scene == "spotify":
            client_id = self.args.spotify_client_id or configured_client_id()
            if not client_id:
                raise SpotifyError("Add --spotify-client-id CLIENT_ID for the Spotify scene.")
            client = SpotifyClient(validate_client_id(client_id), self.spotify_data_dir)
            self.spotify_worker = SpotifyWorker(client, headless=True)
            self.spotify_worker.start()
            if not client.token and self.auto_spotify_login:
                self.spotify_worker.connect()
        elif self.args.scene == "weather" and not self.args.weather_demo:
            self.weather_worker = WeatherWorker(self.args.weather_city)
            self.weather_worker.start()

    def close(self) -> None:
        if self.spotify_worker is not None:
            self.spotify_worker.close()
        if self.weather_worker is not None:
            self.weather_worker.close()

    def frame(self) -> tuple[Image.Image, str]:
        args = self.args
        now = time.monotonic()
        if args.scene == "circle":
            return circle_frame(), "Circle"
        if args.scene == "clock":
            return draw_clock(
                self.fixed_time or datetime.now(), show_numbers=not args.no_numbers,
                show_ticks=not args.no_ticks, theme=args.theme,
                hand_length_scale=getattr(args, "hand_length_percent", 100) / 100,
                hand_width_scale=getattr(args, "hand_width_percent", 100) / 100,
            ), "Clock"
        if args.scene == "moon":
            night = nighttime_reference()
            return render_moon_frame(moon_phase(night)), f"Moon · {night.astimezone():%d %b %Y}"
        if args.scene == "eyes":
            self.eyes.set_rates(
                getattr(args, "eyes_movement_rate", 100) / 100,
                getattr(args, "eyes_blink_rate", 100) / 100, now,
            )
            return render_eyes_frame(self.eyes.pose(now)), "Eyes"
        if args.scene == "walker":
            phase = 2 * math.pi * (now - self.started) / STEP_SECONDS
            return render_walker_frame(phase), "Walker"
        if args.scene == "kintsugi":
            frame = self.kintsugi.frame(
                now, theme=getattr(args, "kintsugi_theme", "indigo"),
                speed=getattr(args, "kintsugi_speed", 100),
                density=getattr(args, "kintsugi_density", 50),
            )
            return frame, f"Kintsugi · {stage_name(self.kintsugi.phase)}"
        if args.scene == "weather":
            if self.weather_worker is not None:
                update = self.weather_worker.latest()
                if update is not None:
                    if update.conditions is not None:
                        self.weather = update.conditions
                    elif update.error:
                        print(f"Weather: {update.error}", flush=True)
            weather = self.weather
            if weather is None:
                return render_weather_frame(demo_weather("fog"), phase=5, show_temperature=False), "Loading weather…"
            return render_weather_frame(
                weather, phase=now,
                local_hour=args.weather_hour if args.weather_demo else None,
            ), f"Weather · {weather.city} · {round(weather.temperature_c)}°C"
        if self.spotify_worker is not None:
            view = self.spotify_worker.latest()
            if view is not None:
                self.spotify_view = view
        return render_spotify_frame(
            self.spotify_view, show_progress=not args.no_progress,
            style=args.spotify_style, rotation_degrees=now * 36,
            show_drift_cover=not args.no_drift_cover,
            drift_blackness=args.drift_blackness / 100,
        ), self.spotify_view.caption


def run_matrix(args, fixed_time: datetime | None = None) -> None:
    from hardware_output import MatrixOutput

    output = MatrixOutput(brightness=args.brightness, gpio_slowdown=args.gpio_slowdown)
    scene = HeadlessScene(args, fixed_time)
    last_image = None
    try:
        scene.start()
        print(f"Displaying {args.scene} on the 64 × 64 matrix. Press Ctrl+C to stop.", flush=True)
        while True:
            frame, caption = scene.frame()
            image = frame.tobytes()
            if image != last_image:
                output.show(frame)
                last_image = image
            if caption != scene.last_caption:
                print(caption, flush=True)
                scene.last_caption = caption
            time.sleep(scene.interval)
    except KeyboardInterrupt:
        pass
    finally:
        scene.close()
        output.close()
