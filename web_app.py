"""Phone-friendly, local-network controls for the 64 × 64 matrix."""

import argparse
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import uvicorn

from control_service import ControlService
from spotify_client import SpotifyError


PAGE = Path(__file__).resolve().parent / "web" / "index.html"


class SceneChange(BaseModel):
    scene: str


class BrightnessChange(BaseModel):
    brightness: int


class SlowdownChange(BaseModel):
    gpio_slowdown: int


def create_app(service: ControlService) -> FastAPI:
    app = FastAPI(title="Matrix control", docs_url=None, redoc_url=None)

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(PAGE)

    @app.get("/api/state")
    def state() -> dict:
        return service.state()

    @app.get("/api/frame.png")
    def frame() -> Response:
        return Response(
            service.frame_png(), media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.put("/api/scene")
    def select_scene(change: SceneChange) -> dict:
        try:
            return service.select_scene(change.scene)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except SpotifyError as error:
            # The current scene remains active if, for example, Spotify has
            # not been configured on the Pi yet.
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.put("/api/brightness")
    def set_brightness(change: BrightnessChange) -> dict:
        try:
            return service.set_brightness(change.brightness)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.put("/api/gpio-slowdown")
    def set_gpio_slowdown(change: SlowdownChange) -> dict:
        try:
            return service.set_gpio_slowdown(change.gpio_slowdown)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Control the LED matrix from a phone on the same Wi-Fi")
    parser.add_argument("--hardware", action="store_true", help="also output frames to the Pi matrix")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 for phones on the local Wi-Fi")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--spotify-client-id", help="public Spotify app Client ID (saved locally)")
    args = parser.parse_args()

    service = ControlService(hardware=args.hardware, spotify_client_id=args.spotify_client_id)
    service.start()
    try:
        print(f"Matrix control: http://{args.host}:{args.port}", flush=True)
        uvicorn.run(create_app(service), host=args.host, port=args.port, workers=1)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
