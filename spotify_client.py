"""Spotify's read-only now-playing API and desktop OAuth (PKCE)."""

import base64
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
from threading import Event
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import webbrowser

from PIL import Image, ImageOps, UnidentifiedImageError


DATA_DIR = Path(__file__).resolve().parent / ".spotify"
ENV_FILE = Path(__file__).resolve().parent / ".env"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEFAULT_PHONE_REDIRECT_URI = "https://aymericschmid.github.io/led-matrix/spotify-callback.html"
SCOPE = "user-read-currently-playing"
TOKEN_URL = "https://accounts.spotify.com/api/token"
PLAYER_URL = "https://api.spotify.com/v1/me/player/currently-playing"
TIMEOUT = 10


class SpotifyError(Exception):
    def __init__(self, message: str, *, retry_after: float = 15, needs_login: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.needs_login = needs_login


def validate_client_id(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", value):
        raise SpotifyError("Enter the 32-character Client ID from your Spotify app settings.")
    return value


def dotenv_value(name: str, path: Path | None = None) -> str:
    """Read a simple KEY=value setting without executing the file as a script."""
    path = path or ENV_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ""


def configured_env_client_id() -> str:
    value = os.environ.get("SPOTIFY_CLIENT_ID", "").strip() or dotenv_value("SPOTIFY_CLIENT_ID")
    return validate_client_id(value) if value else ""


def phone_redirect_uri() -> str:
    value = os.environ.get("SPOTIFY_REDIRECT_URI", "").strip() or dotenv_value("SPOTIFY_REDIRECT_URI")
    value = value or DEFAULT_PHONE_REDIRECT_URI
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SpotifyError("SPOTIFY_REDIRECT_URI must be a complete HTTPS page without a query or fragment.")
    return value


def local_return_origin(value: str) -> str:
    """Permit the browser to return only to a controller on the local network."""
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise SpotifyError("Open the controller using its local Wi-Fi address before connecting Spotify.")
    try:
        port = parsed.port
    except ValueError:
        raise SpotifyError("The controller address has an invalid port.") from None
    try:
        address = ipaddress.ip_address(parsed.hostname)
        private_networks = (
            ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("127.0.0.0/8"),
        )
        allowed = isinstance(address, ipaddress.IPv4Address) and any(
            address in network for network in private_networks
        )
    except ValueError:
        allowed = parsed.hostname == "localhost" or parsed.hostname.endswith(".local")
    if not allowed or port is None:
        raise SpotifyError("Open the controller using its local Wi-Fi address before connecting Spotify.")
    return value


def bridge_state(nonce: str, return_origin: str) -> str:
    origin = local_return_origin(return_origin)
    encoded = base64.urlsafe_b64encode(origin.encode("ascii")).rstrip(b"=").decode("ascii")
    return f"{nonce}.{encoded}"


def configured_client_id() -> str:
    value = configured_env_client_id()
    if value:
        return value
    try:
        config = json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))
        return validate_client_id(config["client_id"])
    except (OSError, ValueError, KeyError, TypeError, SpotifyError):
        return ""


def save_client_id(client_id: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "config.json").write_text(
        json.dumps({"client_id": validate_client_id(client_id)}), encoding="utf-8"
    )


def retry_seconds(header: str | None) -> float:
    try:
        delay = float(header)
    except (TypeError, ValueError):
        try:
            delay = parsedate_to_datetime(header).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            delay = 60
    return max(5, delay)


def request_json(url: str, *, fields: dict | None = None, access_token: str = ""):
    headers = {"Accept": "application/json"}
    data = None
    if fields is not None:
        data = urlencode(fields).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            if response.status == 204:
                return None
            return json.load(response)
    except HTTPError as error:
        if error.code == 429:
            delay = retry_seconds(error.headers.get("Retry-After"))
            raise SpotifyError(
                f"Spotify request limit reached. Retrying in {int(delay)} seconds.", retry_after=delay
            ) from None
        if error.code == 401 or (url == TOKEN_URL and error.code == 400):
            raise SpotifyError("Please reconnect your Spotify account.", needs_login=True) from None
        if error.code == 403:
            raise SpotifyError(
                "Spotify denied access. Check the app's user list, Premium status and permissions.",
                retry_after=60,
            ) from None
        raise SpotifyError(f"Spotify is unavailable (HTTP {error.code}). Retrying shortly.") from None
    except (URLError, OSError, TimeoutError):
        raise SpotifyError("Cannot reach Spotify. Check your connection; retrying shortly.") from None
    except (ValueError, TypeError):
        raise SpotifyError("Spotify returned an unreadable response. Retrying shortly.") from None


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge.rstrip(b"=").decode("ascii")


def callback_result(path: str, expected_state: str) -> tuple[str, str] | None:
    """Ignore unrelated or forged callbacks without ending the login attempt."""
    parsed = urlsplit(path)
    query = parse_qs(parsed.query)
    if parsed.path != "/callback":
        return None
    state = query.get("state", [""])[0]
    if not secrets.compare_digest(state.encode("utf-8"), expected_state.encode("utf-8")):
        return None
    return query.get("code", [""])[0], query.get("error", [""])[0]


def authorization_url(client_id: str, challenge: str, state: str, redirect_uri: str = REDIRECT_URI) -> str:
    query = urlencode({
        "client_id": validate_client_id(client_id), "response_type": "code",
        "redirect_uri": redirect_uri, "scope": SCOPE, "state": state,
        "code_challenge_method": "S256", "code_challenge": challenge,
    })
    return "https://accounts.spotify.com/authorize?" + query


def phone_callback_result(address: str, expected_state: str) -> tuple[str, str]:
    """Validate the full loopback URL copied from a phone's address bar."""
    try:
        parsed = urlsplit(address.strip())
        valid_origin = (
            parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
            and parsed.port == 8888 and not parsed.username and not parsed.password
            and not parsed.fragment
        )
    except ValueError:
        valid_origin = False
    if not valid_origin:
        raise SpotifyError("Paste the complete 127.0.0.1:8888/callback address shown after Spotify sign-in.")
    result = callback_result(parsed.path + "?" + parsed.query, expected_state)
    if result is None or not any(result):
        raise SpotifyError("That Spotify return address is invalid or belongs to another sign-in attempt.")
    return result


@dataclass(frozen=True)
class PlayingTrack:
    title: str
    artist: str
    image_url: str
    spotify_url: str
    is_playing: bool
    progress_ms: int | None = None
    duration_ms: int | None = None
    received_at: float = field(default_factory=time.monotonic)


def milliseconds(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_playback(payload: dict | None) -> PlayingTrack | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("item"), dict):
        return None
    item = payload["item"]
    if item.get("type", "track") != "track":
        return None
    images = (item.get("album") or {}).get("images") or []
    images = [entry for entry in images if isinstance(entry, dict) and entry.get("url")]
    # Prefer the smallest image that still contains at least 64 x 64 pixels.
    suitable = [entry for entry in images if (entry.get("width") or 0) >= 64
                and (entry.get("height") or 0) >= 64]
    image = min(suitable, key=lambda entry: entry["width"] * entry["height"]) if suitable else (
        max(images, key=lambda entry: (entry.get("width") or 0) * (entry.get("height") or 0))
        if images else {}
    )
    artists = ", ".join(artist.get("name", "") for artist in (item.get("artists") or []))
    return PlayingTrack(
        title=item.get("name") or "Unknown track", artist=artists or "Unknown artist",
        image_url=image.get("url", ""),
        spotify_url=(item.get("external_urls") or {}).get("spotify", ""),
        is_playing=bool(payload.get("is_playing")),
        progress_ms=milliseconds(payload.get("progress_ms")),
        duration_ms=milliseconds(item.get("duration_ms")),
    )


def album_frame(data: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as source:
            source = ImageOps.exif_transpose(source)
            # Preserve the whole cover. Letterbox unusual aspect ratios instead of cropping.
            art = ImageOps.contain(source.convert("RGB"), (64, 64), Image.Resampling.LANCZOS)
            frame = Image.new("RGB", (64, 64), "black")
            frame.paste(art, ((64 - art.width) // 2, (64 - art.height) // 2))
            return frame
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise SpotifyError("The album cover could not be read. Retrying shortly.") from None


class SpotifyClient:
    def __init__(self, client_id: str, data_dir: Path = DATA_DIR):
        self.client_id = validate_client_id(client_id)
        self.token_path = data_dir / "tokens.json"
        self.token = {}
        self._cover_url = ""
        self._cover = None
        try:
            saved = json.loads(self.token_path.read_text(encoding="utf-8"))
            if (isinstance(saved, dict) and saved.get("client_id") == self.client_id
                    and isinstance(saved.get("access_token"), str)
                    and isinstance(saved.get("refresh_token"), str)
                    and isinstance(saved.get("expires_at"), (int, float))):
                self.token = saved
        except (OSError, ValueError, TypeError):
            pass

    def _save_token(self, response: dict) -> None:
        if not isinstance(response, dict) or not response.get("access_token"):
            raise SpotifyError("Spotify did not return an access token.", needs_login=True)
        refresh = response.get("refresh_token") or self.token.get("refresh_token")
        if not refresh:
            raise SpotifyError("Spotify did not return a refresh token.", needs_login=True)
        token = {
            "client_id": self.client_id,
            "access_token": response["access_token"],
            "refresh_token": refresh,
            "expires_at": time.time() + float(response.get("expires_in", 3600)),
        }
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.token_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(token, handle)
        temporary.replace(self.token_path)
        self.token = token

    def exchange_code(self, code: str, verifier: str, redirect_uri: str = REDIRECT_URI) -> None:
        # An explicit new login must not inherit another account's refresh token.
        self.token = {}
        self._save_token(request_json(TOKEN_URL, fields={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": self.client_id, "code_verifier": verifier,
        }))

    def authorize(self, stop: Event, *, open_browser: bool = True) -> None:
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)
        result = []

        class Callback(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(1)

            def log_message(self, *_args):
                pass  # Callback URLs contain authorization codes; never log them.

            def do_GET(self):
                answer = callback_result(self.path, state)
                valid = answer is not None and any(answer)
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if valid:
                    result.append(answer)
                    message = "You can return to LED Matrix. It will finish connecting there."
                else:
                    message = "This sign-in callback is not valid. Return to LED Matrix and try again."
                self.wfile.write(message.encode("utf-8"))

        try:
            server = HTTPServer(("127.0.0.1", 8888), Callback)
        except OSError:
            raise SpotifyError("Sign-in port 8888 is busy. Close the other app and try again.",
                               needs_login=True) from None
        with server:
            server.timeout = 0.3
            login_url = authorization_url(self.client_id, challenge, state)
            if open_browser:
                if not webbrowser.open(login_url):
                    raise SpotifyError("Could not open your browser. Please try Connect Spotify again.",
                                       needs_login=True)
            else:
                print("Open this Spotify sign-in URL in your computer's browser:", flush=True)
                print(login_url, flush=True)
                print("Keep SSH port forwarding for 127.0.0.1:8888 open until sign-in completes.",
                      flush=True)
            deadline = time.monotonic() + 180
            while not result and not stop.is_set() and time.monotonic() < deadline:
                server.handle_request()
        if stop.is_set():
            return
        if not result:
            raise SpotifyError("Sign-in timed out. Please try Connect Spotify again.", needs_login=True)
        code, error = result[0]
        if error or not code:
            raise SpotifyError("Spotify sign-in was cancelled. Connect whenever you are ready.",
                               needs_login=True)
        self.exchange_code(code, verifier)

    def access_token(self, *, force_refresh: bool = False) -> str:
        if not self.token:
            raise SpotifyError("Connect Spotify to display your album covers.", needs_login=True)
        if force_refresh or time.time() >= self.token["expires_at"] - 60:
            try:
                self._save_token(request_json(TOKEN_URL, fields={
                    "grant_type": "refresh_token", "refresh_token": self.token["refresh_token"],
                    "client_id": self.client_id,
                }))
            except SpotifyError as error:
                if error.needs_login:
                    self.token = {}
                    self.token_path.unlink(missing_ok=True)
                raise
        return self.token["access_token"]

    def currently_playing(self) -> PlayingTrack | None:
        token = self.access_token()
        try:
            payload = request_json(PLAYER_URL, access_token=token)
        except SpotifyError as error:
            if not error.needs_login:
                raise
            payload = request_json(PLAYER_URL, access_token=self.access_token(force_refresh=True))
        return parse_playback(payload)

    def cover(self, url: str) -> Image.Image:
        if url == self._cover_url and self._cover is not None:
            return self._cover.copy()
        if urlsplit(url).scheme != "https":
            raise SpotifyError("This track has no downloadable album cover.")
        try:
            # Never send Spotify's access token to the image host.
            with urlopen(Request(url), timeout=TIMEOUT) as response:
                data = response.read(5_000_001)
            if len(data) > 5_000_000:
                raise SpotifyError("The album cover is too large to display.")
            frame = album_frame(data)
        except (URLError, OSError, TimeoutError):
            raise SpotifyError("Cannot download the album cover. Retrying shortly.") from None
        self._cover_url, self._cover = url, frame
        return frame.copy()
