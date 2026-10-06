# LED matrix

A Python app for a 64 × 64 RGB LED panel. The same scene renderers drive a desktop emulator and a physical Raspberry Pi HUB75 matrix. The default scene is a light analog clock: a warm off-white background, charcoal hour and minute hands, a golden-yellow second hand, twelve pixel numerals, and sixty tick marks. The original dark clock is also available.

## Run

Requires Python 3.10+, Pillow, and Tkinter (included with most Windows Python installations).

```sh
python -m pip install -r requirements.txt
python main.py
```

The clock follows your computer's local time and updates every second. Close the window or press Escape to quit.

- Press **N** to toggle the numbers.
- Press **T** to toggle the ticks.
- Press **Right / Left** to lengthen / shorten the hands in 5% steps.
- Press **Up / Down** to make the hands bolder / thinner in 10% steps.
- Press **R** to reset hand length and thickness to the original design.
- Press **L** to switch between light and dark clock colors.

Click the emulator window to focus it before using the shortcuts. All three hands adjust together, preserving their relative lengths and thicknesses. The footer shows the current percentages. Length ranges from 60–130% and thickness from 50–250%; longer hands can overlap the numerals. These adjustments work with a frozen `--time` too, and reset when you restart the app.

All twelve numbers use a 3 × 5 pixel font. Tick marks sit outside the numbers, and the second hand is shortened to keep the numerals readable on the small display.

Start directly in the original dark theme with `python main.py --theme dark`. Both themes support all the hand adjustments. Switching themes preserves your current hand length, thickness, and number/tick settings. The light background is part of the actual 64 × 64 RGB frame, so it will also appear on the physical matrix.

To save a preview without opening a window:

```sh
python main.py --snapshot preview.png --time 10:10:35
```

Omit `--time` to use the current local time. You can also use `--time` to freeze the desktop clock for design comparisons, or use `--no-numbers` and `--no-ticks` to start with a simpler face.

The original circle is still available with `python main.py --scene circle`.

## Run on the Raspberry Pi matrix

The desktop emulator remains the default. Add `--output matrix` on the Pi to send the original 64 × 64 RGB frames directly to the HUB75 display. The hardware backend uses the settings from the previously working Python script: one 64 × 64 panel, `regular` GPIO mapping, PWM LSB 50 ns, and brightness 80. GPIO slowdown defaults to 2; adjust it if the panel shows corrupted pixels.

After pushing this folder to your Git repository, clone it into `~/led-matrix` on the Pi. Git will omit the local Spotify tokens and preview/cache files listed in `.gitignore`. The current `rgbmatrix` Python package needs Python 3.11 or newer. Then install the Python dependencies in a virtual environment:

```sh
sudo apt update
sudo apt install -y python3-venv python3-dev python3-pil build-essential git
cd ~/led-matrix
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install git+https://github.com/hzeller/rpi-rgb-led-matrix.git
```

If the `rgbmatrix` build reports `Cannot find the directory "/usr/include/python3.13"`, install the matching headers with `sudo apt install -y python3.13-dev` and repeat the last pip command. If it reports `Imaging.h: No such file or directory`, install `python3-pil`, which provides that Pillow header, and repeat the last pip command. The Python header version must match `.venv/bin/python --version`.

The Pi needs its built-in `snd_bcm2835` sound module disabled for the matrix library's hardware pulse mode. The panel also needs its own 5 V power supply and a ribbon cable to its **IN** connector. After the library's built-in demo works, try:

```sh
cd ~/led-matrix
sudo .venv/bin/python main.py --output matrix --scene circle
sudo .venv/bin/python main.py --output matrix --scene clock
sudo .venv/bin/python main.py --output matrix --scene weather --weather-city Fribourg
sudo .venv/bin/python main.py --output matrix --scene moon
sudo .venv/bin/python main.py --output matrix --scene eyes
sudo .venv/bin/python main.py --output matrix --scene walker
```

Press **Ctrl+C** to clear the panel and stop. The command-line scene options used by the emulator also work on the matrix, including `--theme`, `--no-progress`, `--spotify-style`, `--no-drift-cover`, and `--weather-demo`. Use `--brightness 1..100` and `--gpio-slowdown 0..10` to tune the hardware. There is no keyboard scene cycling on the headless Pi yet; select a scene when launching it.

The matrix driver initializes with root access under `sudo` and then drops back to the invoking account. That lets Spotify save its token under the project's `.spotify` folder. Keep the project in that account's home directory and launch it with `sudo` from that account.

### Phone controller on your Wi-Fi

The first phone-control version is a small web app served by the Pi. It lets you select any scene, see the actual 64 × 64 frame as an LED-dot preview, adjust brightness, and save GPIO slowdown. Settings persist in `.matrix/control.db`, which Git ignores. Brightness changes immediately; GPIO slowdown takes effect when the controller restarts. Scheduling and Spotify sign-in from the phone are planned for later milestones.

Install the additional web dependencies in the existing virtual environment:

```sh
cd ~/led-matrix
.venv/bin/python -m pip install -r requirements-web.txt
```

Stop any existing `main.py --output matrix` process, then start the controller:

```sh
sudo .venv/bin/python web_app.py --hardware --host 0.0.0.0
```

On a phone connected to the same Wi-Fi, open **http://matrix.local:8000**. If that name does not resolve on your phone, use **http://YOUR_PI_IP:8000** instead. Leave the terminal running while using the controller; **Ctrl+C** stops it and clears the matrix. Do not forward port 8000 on your router: this first version has no sign-in and is intended only for a trusted local network.

To try the phone page on a computer without a matrix, install `requirements-web.txt` and run `python web_app.py`. Then open **http://127.0.0.1:8000**. The page labels this mode “Preview only.”

If your Pi already has a Spotify token, the Spotify scene can use it. Otherwise, you can supply the public Client ID once with `--spotify-client-id YOUR_CLIENT_ID`. The current headless sign-in still needs the SSH tunnel described below; connecting directly from the phone will be a later milestone.

### Spotify on the headless Pi

The Spotify app still uses `http://127.0.0.1:8888/callback` for sign-in. Because the Pi has no browser, open an SSH session **with port forwarding** from your computer before starting the scene:

```sh
ssh -L 8888:127.0.0.1:8888 matrix@matrix.local
```

In that SSH session, start the matrix scene with your Spotify app's public Client ID:

```sh
cd ~/led-matrix
sudo .venv/bin/python main.py --output matrix --scene spotify --spotify-client-id YOUR_CLIENT_ID
```

The Pi prints a sign-in URL. Open it in your computer's browser. The SSH tunnel carries Spotify's loopback callback to the Pi. Sign-in is needed only on first use or if Spotify revokes access; the refresh token is saved locally on the Pi. Your Spotify account must have permission to use the app. Never put a Client Secret in the command or repository.

## Animations

```sh
python main.py --scene eyes
python main.py --scene walker
python main.py --scene eyes --snapshot .preview/eyes-idle.png
python main.py --scene walker --snapshot .preview/walker.png
```

Press **D** to cycle between the two animations. Pale green eyes stare out of a nearly black face, shifting their gaze at irregular intervals and blinking independently or together. The walker is seen from the side, heading left through a dim night street. A moonlit coat, warm scarf, alternating steps, and ground drifting to the right give the small silhouette its motion. Both animations run entirely offline.

## Moon

```sh
python main.py --scene moon
python main.py --scene moon --snapshot .preview/moon-tonight.png
```

A large textured Moon fills almost the entire 64 × 64 panel. Its lit side reflects the phase for the current night (at midnight if it is before 06:00 locally) or the coming night (at 22:00 otherwise). The unlit surface stays faintly visible. The scene uses the computer's local time and works offline. The phase is an approximation based on the [US Naval Observatory's 2026 new-moon time](https://aa.usno.navy.mil/calculated/moon/phases?date=2026-10-01&format=p&nump=6) and the [mean synodic month](https://aa.usno.navy.mil/downloads/c15_usb_online.pdf); individual lunations can vary by hours. The lighting direction follows the [Northern Hemisphere view described by NASA](https://science.nasa.gov/moon/top-moon-questions/).

## Weather glass

```sh
python main.py --scene weather
```

Weather glass starts with **Fribourg, Switzerland** and shows a small animated sky for the current conditions. The sun follows the local sunrise-to-sunset arc, and the sky shifts from warm dawn to bright daytime colours, sunset, and a dark night. Clouds drift, angled rain streaks travel down-left, snow flakes float, fog softens the horizon, and storms briefly flash. The temperature stays visible on the matrix. Use **Change city** to choose another location. The weather updates in the background every 15 minutes; connection problems are shown below the matrix and retried.

Preview the designs without internet access by starting a demo. Press **D** or **Next weather** to cycle through clear, cloud, rain, snow, fog, and storm. Press **Left** or **Right** to move the time back or forward by one hour; every weather condition can be viewed by day or night. The default demo time is 13:00, and `--weather-hour` sets a different starting hour:

```sh
python main.py --scene weather --weather-demo rain
python main.py --scene weather --weather-demo snow --weather-hour 23 --snapshot .preview/weather-snow-night.png
```

For a different live location, use `--weather-city "Paris, France"`. Weather data and city lookup come from [Open-Meteo](https://open-meteo.com/en/docs) and its [geocoding service](https://open-meteo.com/en/docs/geocoding-api). The free service needs no API key for personal use; its [terms](https://open-meteo.com/en/terms) require attribution, which is shown in the emulator. The demo scenes use illustrative sample conditions and never claim to be live weather.

## Spotify album covers

```sh
python main.py --scene spotify
```

The Spotify view reads the current song from your account, including music playing on another phone, computer, or speaker. It displays the whole album cover inside a thin progress frame coloured to match the artwork on the 64 × 64 panel. Track title, artist, playback status, and an **Open in Spotify** link appear below the matrix, keeping the artwork unobstructed.

Press **P** in the Spotify view, or use the **Progress: ON/OFF** button, to switch the frame off and show the album cover across the full 64 × 64 panel. The setting applies immediately and resets to on when the app restarts. For a fixed snapshot, use `python main.py --scene spotify --snapshot .preview/spotify.png --no-progress`.

Press **V**, or use the style button, to cycle through four display styles:

- **COVER** — the full album cover with its progress frame.
- **VINYL + SPIN** — a miniature record with grooves, a moving light reflection, tonearm, and a round center cover that rotates in the same direction as the reflection.
- **VINYL + STILL COVER** — the reflection rotates across the record while the round center cover keeps its orientation.
- **SOFT COLOUR DRIFT** — several distinct, vivid colours sampled from the artwork drift gently around the readable cover while music is playing.

You can start directly in any style with `python main.py --scene spotify --spotify-style colour-drift` (or `cover`, `vinyl-spin`, or `vinyl-still`). Each style uses the existing album cover and Spotify polling; no audio analysis is requested.

In **SOFT COLOUR DRIFT**, press **H** or use the **Cover: ON/OFF** button to hide the album art. The colour fields become brighter when the cover is hidden, turning the matrix into a full-panel ambient animation. Use the **Black amount** slider, press **-** to make it darker, or press **=** to make it more colourful. For a snapshot, combine `--no-drift-cover` with `--drift-blackness PERCENT`.

In both vinyl versions, the stylus starts close to the center label and moves outward toward the edge as the track progresses. The complete tonearm rotates around its pivot with it, holds its current groove while paused, and updates again after Spotify reports a seek or track change. A broad, feathered diagonal reflection rotates across the record to make its motion visible without adding a colored line.

### Connect your account

1. Open the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and create or select your app with Web API access.
2. In the app's settings, add **exactly** `http://127.0.0.1:8888/callback` to its Redirect URIs, then save. Spotify does not accept `localhost` here.
3. Add your listening account under **Users and Access** if needed. Spotify currently requires the app owner to have **Premium** for development-mode apps.
4. Start the Spotify scene and click **Connect Spotify**. If prompted, paste the app's **Client ID**, not its Client Secret.
5. Sign in through Spotify in your browser and approve access to your currently playing content. Return to the emulator and play a song.

For emulator sign-in, the browser and emulator must run on the same computer. On the Pi, the SSH tunnel described above connects your computer's browser to the headless app. The app requests only `user-read-currently-playing`; it does not play, pause, or modify anything in your Spotify account. PKCE authorization means no Client Secret is needed.

The public Client ID is saved in `.spotify/config.json`. Login credentials are stored locally in `.spotify/tokens.json`, refreshed automatically, and excluded from Git. Keep that folder private. To switch accounts, close the app, delete `.spotify/tokens.json`, then connect again. You may also set the `SPOTIFY_CLIENT_ID` environment variable instead of using the saved configuration.

### Display behavior

- Checks Spotify every 5 seconds on a background thread; the window stays responsive.
- Displays the cover at 56 × 56 pixels, surrounded by a dark gap and a one-pixel progress frame. The frame fills clockwise from the top-left corner as the song plays.
- Chooses a prominent colour from each album cover for the filled border, with a dim version for the remaining portion. Dark accents are brightened for readability; monochrome covers get a neutral border. Colour extraction runs only when a new view arrives, not on every animation frame.
- Advances the frame locally between polls, with a gradual brightness change at the leading pixel. Pauses, seeks, and new tracks resynchronize on the next Spotify update, normally within about 5 seconds. It stops at the end of a track until the next update.
- Uses the existing permission and polling interval; no extra sign-in or additional Spotify requests are needed for progress. If Spotify supplies no usable duration or position, the cover fills the panel without a progress frame.
- Downloads artwork only when its image URL changes. The most recent cover is cached in memory.
- Preserves the whole artwork without cropping or stretching; unusual aspect ratios get black borders.
- Keeps the cover visible when paused and labels it **PAUSED**, while Spotify still returns the track.
- Shows a music-note placeholder for no active track, unsupported content such as podcasts, or missing artwork.
- Reports connection problems, retries transient failures, honors Spotify's `Retry-After` limits, and offers reconnection when authorization expires.

After connecting, you can also save the current cover as an LED preview:

```sh
python main.py --scene spotify --snapshot .preview/spotify.png
```

If Spotify rejects sign-in, check the exact Redirect URI and Client ID. If it returns an access error, check the account allowlist and the app owner's Premium status. If port 8888 is busy, close the other program using it before connecting.

References: [PKCE authorization](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow), [redirect URIs](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri), [currently playing endpoint](https://developer.spotify.com/documentation/web-api/reference/get-the-users-currently-playing-track), [development-mode requirements](https://developer.spotify.com/documentation/web-api/concepts/quota-modes).

Run the offline integration checks with `python -m unittest discover -s tests -v`. These use synthetic responses and require no account or network access.

## Drawing

Edit `draw_clock()` in `clock_face.py` for the clock, or `draw_circle()` in `main.py` for the original circle. Each returns an ordinary Pillow RGB image with exactly 64 × 64 pixels. Coordinates run from `(0, 0)` in the top-left to `(63, 63)` in the bottom-right.

`MatrixEmulator.show(frame)` displays that image as individual LED dots. The dots are only a desktop preview: spacing and smoothing do not alter the original image. `MatrixOutput.show(frame)` sends the same RGB frame to an offscreen `rgbmatrix` canvas and swaps it onto the physical panel at vertical sync.

The clock face is cached, and a fresh frame is drawn only when the second or a display setting changes. Hand edges use intermediate LED brightness values for softer diagonals.

Physical brightness is set at launch with `--brightness`. Plexiglass diffusion remains a future physical-design step.

## Hardware reference

- [Joy-IT LED-Matrix01 specifications](https://www.joy-it.net/en/products/LED-Matrix01)
- [Joy-IT matrix and controller manual](https://www.joy-it.net/files/files/Produkte/RB-MatrixCtrl/RB-MatrixCtrl_Manual_2021-08-05.pdf)
- [rpi-rgb-led-matrix Python documentation](https://github.com/hzeller/rpi-rgb-led-matrix/blob/master/bindings/python/README.md)

The hardware configuration uses the user's previously working `regular` GPIO mapping. The exact controller model can still be confirmed from its board marking.
