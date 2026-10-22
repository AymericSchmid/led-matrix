# Google Home through Sinric Pro

Use one Sinric custom device named **Matrice** for power, brightness and scenes.
The phone and voice interfaces use the same `.matrix/control.db`. Scene appearance
settings and Spotify account connection remain in the phone controller.

## Install and configure

Use the existing Python environment for the controller, with Python 3.10 or later
(retain the README's Pi hardware Python requirements).

```sh
python -m pip install -r requirements-web.txt -r requirements-sinric.txt
```

The integration pins `sinricpro==6.0.0`. Standalone rendering needs only
`requirements.txt`. Sinric is optional and is imported only when configured.

1. Sign in to the [Sinric portal](https://portal.sinric.pro/). Under Device Templates,
   create a template starting with the **Light** device type. Add exactly **Power**,
   **Brightness**, and **Mode**. The [official template guide](https://help.sinric.pro/pages/custom-templates)
   explains capability limits tied to licenses and warns that Google Home support
   depends on device type. Confirm the portal accepts this combination.
2. Configure Mode as controllable, choose the French locale, and name the mode
   `Scène`. Copy its exact instance ID. Configure these wire values and French
   labels; the wire values must remain the lowercase IDs below.

   | Wire value | French label |
   | --- | --- |
   | `clock` | Horloge |
   | `circle` | Cercle |
   | `spotify` | Spotify |
   | `weather` | Météo |
   | `moon` | Lune |
   | `eyes` | Yeux |
   | `walker` | Marcheur |
   | `kintsugi` | Kintsugi |
   | `shadow` | Ombre |

   The [Mode guide](https://help.sinric.pro/pages/device-templates/capabilities/mode)
   describes locales and Google's Modes trait. Discovery and exact French speech
   for this particular template still need live verification.
3. Save the template, create one device from it, and name it **Matrice** (or your
   preferred name). Copy its device ID and the associated app key and app secret.
4. Copy `.env.example` to `.env` in the project root. Uncomment and replace all four
   Sinric entries together. Use placeholders only in any shared notes:

   ```dotenv
   SINRIC_APP_KEY=your-uuid-from-sinric
   SINRIC_APP_SECRET=your_sinric_secret_at_least_32_characters
   SINRIC_DEVICE_ID=your_24_hex_device_id
   SINRIC_SCENE_INSTANCE_ID=your_exact_mode_instance_id
   ```

   The key must be a UUID, the secret at least 32 characters, and device ID 24 hex
   characters. Keep credentials in the ignored `.env`; `docs/` is publicly hosted.
   A nonempty environment variable overrides `.env`; empty or whitespace variables
   fall back to the file. Restart the controller after configuration changes.
5. In Google Home, add/link **Sinric Pro** through “Works with Google Home” and
   select your device/room. Refresh or ask Google to synchronize devices after
   changing the template. If Light with these three capabilities is rejected or
   not discovered, record the exact portal/discovery error for a design adjustment;
   do not replace it with several virtual devices.
6. Launch the existing single controller process:

   ```sh
   python web_app.py --host 0.0.0.0
   # Raspberry Pi hardware, using the environment and privileges from README:
   sudo .venv/bin/python web_app.py --hardware --host 0.0.0.0
   ```

   Follow the README's existing hardware/systemd instructions for deployment. Add
   the optional requirements to that same environment and retain its privilege
   configuration. Run one worker and one hardware owner. Sinric connects outbound;
   no router port forwarding is required. Keep the phone controller on your LAN.

## Control behavior and troubleshooting

- Off blanks the panel and preview, while the Pi and data workers keep running.
  Restart restores off without displaying a scene first.
- Power on restores the selected scene and last nonzero brightness (1–100).
  Brightness zero turns off and preserves that level. Positive brightness changes
  do not turn the display on. Relative brightness clamps the remembered level plus
  delta into 0–100 and follows the same rules.
- Selecting any scene turns on, including reselecting the current one while off.
  A failed activation preserves scene and power. Configure Spotify before choosing
  its scene. If a saved scene fails at startup, select another scene or fix its
  configuration and reselect it; `scene_error` explains the recovery path.
- Phone changes are reported to Sinric; reconnect publishes the latest state.
  Failed reports retry current values, with no historical edit queue.
- The separate Sinric status shows `disabled`, `connecting`, `connected`, or `error`.
  No config means disabled; partial config, missing SDK, or startup errors leave
  HTTP and rendering available. Complete all four values, install the optional
  requirements, check internet/credentials in the portal, and restart as needed.
  `connecting` also covers SDK reconnect attempts; local Connected is independent.
- A successful socket send is not a server receipt. Every reconnect republishes
  current power, remembered brightness, and mode. Cloud state never overwrites local
  saved settings on connection.

## Acceptance record — 2026-10-22

Offline automated coverage includes strict service/HTTP/SDK input, power/relative
brightness, persistence and legacy migration, scene activation/recovery, fake
hardware black frames, slow-scene wake-up, catalogue/CLI metadata, configuration
precedence, real pinned SDK contracts with patched network, reporting races,
reconnects, and shutdown. Tests use temporary settings and dummy credentials.
The preview server starts; browser visual inspection is pending because this
execution session has no browser/app surface. Complete this phone check at a
390px viewport: all nine English labels/icons, power On/Off and aria-pressed,
remembered slider while off, black preview, current-scene reselect, and separate
disabled/error status while local Connected remains active.

All live checks below are **pending**; automated tests do not establish French
recognition, custom-template acceptance, or actual Pi output:

- [ ] Record Pi model, OS/Python, SDK version (planned 6.0.0), accepted device type,
      template locale, Mode instance ID (no keys/secrets), and Google Home locale.
- [ ] French on/off: try « Allume la matrice » and « Éteins la matrice ».
- [ ] Absolute brightness: try « Règle la luminosité de la matrice à 40 % »;
      include zero and confirm remembered brightness restores on power on.
- [ ] Relative brightness up/down, boundaries, and adjustments while off.
- [ ] All nine modes, including « Mets la matrice en mode horloge »; record the
      actual recognized phrases and results for each label in the table.
- [ ] Phone-to-cloud power, brightness and scene reports; rapid edits converge.
- [ ] Failed Spotify activation leaves the previous scene/power unchanged.
- [ ] Restart while off produces no flash and restores remembered brightness.
- [ ] Interrupt internet: local controls and output work, reconnect shows latest
      local settings with no replay of old edits.
- [ ] Record exact incompatibility if the portal or Google discovery rejects the
      template; request a focused design change before altering device topology.

Record observations/date next to each item when performed; no live account changes
were made during offline implementation.
