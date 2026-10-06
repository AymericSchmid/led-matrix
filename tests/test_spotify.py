"""Offline checks for account handling, polling, and real-size cover rendering."""

from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import urlencode

from PIL import Image, ImageChops

import spotify_client as spotify
from main import MatrixEmulator, render_leds
from spotify_scene import SpotifyWorker, SpotifyView, waiting_frame


CLIENT_ID = "0123456789abcdef0123456789abcdef"


def playback(*, playing=True, image="https://i.scdn.co/image/test"):
    return {
        "is_playing": playing,
        "item": {
            "type": "track", "name": "Test song", "artists": [{"name": "Test artist"}],
            "album": {"images": [
                {"url": "https://i.scdn.co/image/large", "width": 640, "height": 640},
                {"url": image, "width": 64, "height": 64},
            ]},
            "external_urls": {"spotify": "https://open.spotify.com/track/test"},
        },
    }


class SpotifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = spotify.SpotifyClient(CLIENT_ID, Path(self.temp.name))

    def token(self, expires_in=3600):
        self.client._save_token({
            "access_token": "test-access", "refresh_token": "test-refresh", "expires_in": expires_in,
        })

    def test_pkce_and_callback_state(self):
        verifier, challenge = spotify.pkce_pair()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        self.assertEqual(len(challenge), 43)
        self.assertNotIn("=", challenge)
        self.assertIsNone(spotify.callback_result("/callback?state=forged&code=code", "expected"))
        self.assertIsNone(spotify.callback_result("/favicon.ico?state=expected", "expected"))
        self.assertIsNone(spotify.callback_result("/callback?state=%C3%A9&code=code", "expected"))
        self.assertEqual(spotify.callback_result("/callback?state=expected&code=code", "expected"), ("code", ""))
        self.assertEqual(spotify.callback_result("/callback?state=expected&error=access_denied", "expected"), ("", "access_denied"))

    def test_phone_callback_requires_exact_loopback_address_and_state(self):
        good = "http://127.0.0.1:8888/callback?code=example&state=expected"
        self.assertEqual(spotify.phone_callback_result(good, "expected"), ("example", ""))
        for address in (
            "http://matrix.local:8888/callback?code=example&state=expected",
            "http://127.0.0.1:8000/callback?code=example&state=expected",
            "https://127.0.0.1:8888/callback?code=example&state=expected",
            "http://127.0.0.1:8888/callback?code=example&state=wrong",
            "http://127.0.0.1:8888/other?code=example&state=expected",
        ):
            with self.subTest(address=address), self.assertRaises(spotify.SpotifyError):
                spotify.phone_callback_result(address, "expected")

    def test_first_run_requires_login_without_network(self):
        with patch.object(spotify, "request_json") as request:
            with self.assertRaises(spotify.SpotifyError) as raised:
                self.client.currently_playing()
            self.assertTrue(raised.exception.needs_login)
            request.assert_not_called()

    def test_refresh_retains_or_rotates_refresh_token(self):
        self.token(expires_in=-1)
        with patch.object(spotify, "request_json", return_value={"access_token": "new", "expires_in": 3600}) as request:
            self.assertEqual(self.client.access_token(), "new")
            self.assertEqual(request.call_args.kwargs["fields"]["client_id"], CLIENT_ID)
        self.assertEqual(self.client.token["refresh_token"], "test-refresh")
        with patch.object(spotify, "request_json", return_value={
            "access_token": "newer", "refresh_token": "rotated", "expires_in": 3600,
        }):
            self.assertEqual(self.client.access_token(force_refresh=True), "newer")
        reloaded = spotify.SpotifyClient(CLIENT_ID, Path(self.temp.name))
        self.assertEqual(reloaded.token["refresh_token"], "rotated")

    def test_invalid_refresh_token_is_discarded(self):
        self.token(expires_in=-1)
        with patch.object(spotify, "request_json", side_effect=spotify.SpotifyError("Reconnect", needs_login=True)):
            with self.assertRaises(spotify.SpotifyError):
                self.client.access_token()
        self.assertFalse(self.client.token)
        self.assertFalse(self.client.token_path.exists())

    def test_cache_cannot_be_reused_by_another_client(self):
        self.token()
        other = spotify.SpotifyClient("a" * 32, Path(self.temp.name))
        self.assertFalse(other.token)

    def test_revoked_access_token_refreshes_and_retries_once(self):
        self.token()
        with patch.object(spotify, "request_json", side_effect=[
            spotify.SpotifyError("Unauthorized", needs_login=True),
            {"access_token": "fresh", "expires_in": 3600}, playback(),
        ]) as request:
            self.assertEqual(self.client.currently_playing().title, "Test song")
            self.assertEqual(request.call_count, 3)
            self.assertEqual(request.call_args.kwargs["access_token"], "fresh")

    def test_rate_limit_does_not_trigger_token_refresh(self):
        self.token()
        with patch.object(spotify, "request_json", side_effect=spotify.SpotifyError("Wait", retry_after=120)) as request:
            with self.assertRaises(spotify.SpotifyError) as raised:
                self.client.currently_playing()
            self.assertEqual(raised.exception.retry_after, 120)
            self.assertEqual(request.call_count, 1)

    def test_http_429_and_no_content(self):
        failure = HTTPError(spotify.PLAYER_URL, 429, "Rate limit", {"Retry-After": "75"}, None)
        with patch.object(spotify, "urlopen", side_effect=failure):
            with self.assertRaises(spotify.SpotifyError) as raised:
                spotify.request_json(spotify.PLAYER_URL)
            self.assertEqual(raised.exception.retry_after, 75)
        response = Mock(status=204)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(spotify, "urlopen", return_value=response):
            self.assertIsNone(spotify.request_json(spotify.PLAYER_URL))

    def test_playback_variants_and_thumbnail_selection(self):
        track = spotify.parse_playback(playback(playing=False))
        self.assertFalse(track.is_playing)
        self.assertEqual(track.artist, "Test artist")
        self.assertEqual(track.image_url, "https://i.scdn.co/image/test")
        self.assertIsNone(spotify.parse_playback(None))
        self.assertIsNone(spotify.parse_playback({"item": None}))
        self.assertIsNone(spotify.parse_playback({"item": {"type": "episode"}}))
        data = playback()
        data["item"]["album"]["images"] = []
        self.assertEqual(spotify.parse_playback(data).image_url, "")

    def test_album_resizing_preserves_entire_image(self):
        source = Image.new("RGB", (128, 64), (240, 20, 80))
        source.paste((0, 120, 240), (96, 0, 128, 64))
        data = BytesIO()
        source.save(data, format="PNG")
        frame = spotify.album_frame(data.getvalue())
        self.assertEqual((frame.mode, frame.size), ("RGB", (64, 64)))
        self.assertEqual(frame.getpixel((32, 0)), (0, 0, 0))
        self.assertEqual(frame.getpixel((0, 32)), (240, 20, 80))
        self.assertEqual(frame.getpixel((63, 32)), (0, 120, 240))

    def test_cover_cache_does_not_send_bearer_token_or_mutate(self):
        data = BytesIO()
        Image.new("RGB", (64, 64), (30, 100, 210)).save(data, format="PNG")
        response = Mock()
        response.read.return_value = data.getvalue()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(spotify, "urlopen", return_value=response) as request:
            first = self.client.cover("https://i.scdn.co/image/test")
            first.paste((255, 0, 0), (0, 0, 64, 64))
            second = self.client.cover("https://i.scdn.co/image/test")
            self.assertEqual(second.getpixel((32, 32)), (30, 100, 210))
            self.assertEqual(request.call_count, 1)
            self.assertNotIn("Authorization", dict(request.call_args.args[0].header_items()))

    def test_paused_track_and_no_playback_clear_old_art(self):
        client = Mock()
        client.currently_playing.side_effect = [spotify.parse_playback(playback(playing=False)), None]
        client.cover.return_value = Image.new("RGB", (64, 64), "red")
        worker = SpotifyWorker(client)
        paused = worker.poll_once()
        self.assertIn("PAUSED", paused.caption)
        self.assertEqual(paused.frame.getpixel((0, 0)), (255, 0, 0))
        idle = worker.poll_once()
        self.assertFalse(idle.spotify_url)
        self.assertIsNone(ImageChops.difference(idle.frame, waiting_frame()).getbbox())

    def test_worker_waits_for_login_and_shuts_down(self):
        worker = SpotifyWorker(self.client)
        worker.start()
        view = worker.views.get(timeout=2)
        self.assertTrue(view.needs_login)
        worker.close()
        worker.thread.join(timeout=2)
        self.assertFalse(worker.thread.is_alive())

    def test_ui_applies_updates_and_disables_stale_track_link(self):
        app = MatrixEmulator.__new__(MatrixEmulator)
        app.show, app.caption, app.connect_button, app.track_button = Mock(), Mock(), Mock(), Mock()
        app._last_frame_key = None
        view = SpotifyView(waiting_frame(), "Song", "https://open.spotify.com/track/test")
        app.apply_spotify_view(view)
        app.apply_spotify_view(view)
        self.assertEqual(app.show.call_count, 1)
        app.apply_spotify_view(SpotifyView(waiting_frame(), "Reconnect", needs_login=True))
        self.assertEqual(app.spotify_url, "")
        self.assertEqual(app.connect_button.configure.call_args.kwargs["state"], "normal")
        self.assertEqual(app.track_button.configure.call_args.kwargs["state"], "disabled")


if __name__ == "__main__":
    unittest.main()
