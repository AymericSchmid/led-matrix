# Google Home control through Sinric Pro

Date: 2026-10-22
Status: Proposed written specification; awaiting user review.

## Intent and agreed scope

Control the existing 64 x 64 LED matrix through Google Home in French, using
Sinric Pro as the bridge. Version one supports power, brightness, and all
existing scenes. Individual scene settings remain available through the phone
controller and are outside the Google Home scope for this version.

The user explicitly prioritizes coherent behavior, straightforward future
development, and maintainability, and authorizes focused improvements to the
existing code. The conversational design was approved before this document was
written. Implementation follows review of this specification and its subsequent
implementation plan.

Success means the phone and voice controls operate the same saved state, scene
registration is shared, and loss of cloud connectivity does not prevent local
control. Tests must exercise control behavior without real hardware or cloud
credentials. A separate live check verifies French Google Home commands.

## Existing structure

- `control_service.py` owns persistent settings, a lock, one render loop, and
  the active `HeadlessScene`. Settings are saved in `.matrix/control.db`.
- `web_app.py` translates HTTP requests into service calls and starts the
  controller. `web/index.html` polls its state and frame preview.
- `headless_scene.py` manages scene rendering and data workers; individual
  scene modules implement artwork and animation.
- `hardware_output.py` handles the HUB75 device and accepts brightness 1..100.
- `main.py` supplies the standalone desktop emulator, snapshots, and direct
  matrix runner. The scene choices are duplicated there and in the service;
  the web page maintains a separate table of scene labels and icons.
- `.env` parsing currently lives in `spotify_client.py` despite being useful to
  other integrations. There is no persistent display power state.

## Architecture and alternatives

Use one Sinric custom device with Power, Brightness, and Mode capabilities.
Mode represents the mutually exclusive scene choice. Configure its French
locale and use `Matrice` as the documented device name; the user may rename it.

The recommended adapter runs in the existing controller process and calls
`ControlService` directly. This keeps one hardware owner, one saved state, and
one process to start on the Pi. A separate bridge process calling HTTP would
provide process isolation but introduce another service and a network boundary
without a current requirement. Separate Sinric scene devices would add portal
configuration and duplicate device entries; a single Mode capability fits the
existing scene selector.

Data flows are:

```text
Google Home <-> Sinric Pro <-> Sinric adapter --+
                                              +-> ControlService -> renderer -> panel
Phone browser <-> HTTP adapter ---------------+        |
                                                       +-> saved settings / preview
```

`ControlService` owns decisions and validation. Adapters translate requests,
errors, and state; they never implement their own scene or power rules. Sinric
network calls must not run under the service lock or on the render thread.

## Shared scene catalogue

Introduce a small catalogue module containing each scene's stable ID, existing
English display name, French voice label, icon, and allowed setting names.
Service validation, CLI choices, and HTTP scene metadata derive from it. The
phone page builds its scene labels and buttons from server metadata, removing
its hard-coded label table. Preserve existing English phone labels; French
voice support does not require translating the entire application.

| Internal ID | French label |
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

Internal IDs remain the persistence/API identifiers. The adapter explicitly
maps configured Sinric mode values to these IDs; it does not interpret free-form
speech. French labels are configured in Sinric's template. Unknown modes and
unexpected mode instance IDs are rejected.

Keep scene implementations and their existing lifecycle in `HeadlessScene`.
This change does not require a dynamic plugin loader or a rewrite of the
desktop emulator. A developer guide must distinguish adding a renderer from
registering its metadata and updating the Sinric template. Catalogue changes
cannot silently update a manually configured cloud template; document the
portal update and Google Home refresh steps.

## State and behavior

Add a persisted boolean `power`, defaulting to true for existing databases.
The stored `brightness` remains the last nonzero level in 1..100. Power is
independent of that level; display-off never requires a hardware brightness of
zero. Preserve existing scene IDs, settings, and legacy settings migration.

| Command or event | Required behavior |
| --- | --- |
| Power off | Output a black frame and expose a black preview; keep the controller reachable. |
| Power on | Display the selected scene at the remembered brightness. |
| Select a scene | Activate it and turn power on, including reselecting the current scene while off. |
| Brightness 1..100 | Save and apply the level without changing power. |
| Brightness 0 | Turn off without replacing the remembered nonzero level. |
| Relative brightness adjustment | Apply the delta to the remembered level, clamp to 0..100, then use the same brightness rules. |
| Change individual scene settings | Keep the existing behavior and do not implicitly turn power on. |
| Restart | Restore power, brightness, and scene; legacy state starts powered on. |

Brightness accepts integers, excluding booleans. Out-of-range absolute values
and malformed requests fail consistently through both adapters. Relative
brightness read/modify/write is performed under the service lock.

Off is display blanking, not Pi shutdown or animation pause. Existing data
workers can continue running; on displays current time, weather, or playback.
The off branch must blank the output independently of successful scene rendering.
Power transitions wake rendering immediately, including scenes with long frame
intervals such as the Moon. Clear cached frame comparisons as needed so on/off
cannot leave an old image on the panel. On startup, a saved off state must not
flash a scene before blanking.

Scene activation validates and prepares the replacement before committing the
new scene and power state. If activation fails, preserve the previous scene
and power and return an error. For example, selecting unconfigured Spotify
must not claim success or turn an off display on. If a saved scene cannot be
started at controller startup, keep the controller available with a black
frame and an actionable error so another scene can be selected; do not silently
overwrite the saved scene with a different one.

## State synchronization and errors

Expose a consistent state snapshot with a monotonically increasing in-process
revision for accepted state changes. The adapter tracks the latest relevant
power, brightness, and scene values, coalescing rapid updates rather than
keeping an unbounded event history. Repeated commands that do not change state
succeed without saving or publishing unnecessary changes.

Phone-originated changes are reported to Sinric. Voice commands receive success
only after the service accepts them. Avoid duplicate unsolicited reports for
values already acknowledged to the cloud, using the command result's revision
so a concurrent phone edit is not accidentally suppressed. Cloud state reports
do not feed back into local command handling.

On connection or reconnection, publish a fresh service snapshot. Do not replay
old local edits or apply a cached cloud snapshot over current local state.
Failed reports are retried with the newest state. Incoming commands still use
the ordinary validation path. Use the SDK's reconnect behavior where available;
do not build a second competing reconnect loop.

Cloud failures leave rendering and local HTTP controls running. Surface a
small integration status in the phone controller: disabled, connecting,
connected, or error, with a safe actionable message. Invalid credentials or
incomplete configuration must not stop the local controller. Logs and API
responses must never disclose keys or signed payloads containing secrets.

## Configuration and lifecycle

Move reusable `.env` reading into a neutral configuration module. Preserve
Spotify's current resolution semantics: a nonempty environment variable takes
precedence over the project `.env`. Retain compatibility wrappers where existing
callers or tests depend on the Spotify helper names.

Document these settings in `.env.example` using placeholders only:

- `SINRIC_APP_KEY`
- `SINRIC_APP_SECRET`
- `SINRIC_DEVICE_ID`
- `SINRIC_SCENE_INSTANCE_ID`

No Sinric configuration means disabled. Partial configuration produces an
actionable status and leaves local control usable. Keep Sinric dependencies
optional for standalone rendering; install a tested, bounded SDK version via
a dedicated integration requirements file. If configured but unavailable, the
adapter reports the missing dependency without preventing local startup.

Start and stop the adapter with `web_app.py`'s controller lifecycle. Give its
asynchronous network work an explicit owner and clean cancellation/join path.
Stop new cloud commands and close the adapter before shutting down the service
and releasing matrix hardware. Startup failures must clean up resources already
opened. The existing single-worker deployment remains the supported mode.

No router port forwarding is required: the adapter initiates the cloud
connection. Sinric credentials stay in the ignored `.env`, separate from public
scene metadata and the SQLite control settings.

## API and phone controller

Add a power endpoint and an on/off control. Extend state with power, scene
metadata, and integration status while retaining existing fields, including
the scene ID list, for compatibility. Update brightness validation to permit
the explicit zero-as-off behavior; the existing phone brightness slider may
remain 1..100 because power has its own control.

Show the remembered brightness while off, clearly accompanied by the off
state. The preview remains black until the display is on. Use the same scene
selection behavior from either interface. Existing scene settings and Spotify
account flows continue through the shared service.

## Verification and acceptance

Automated tests must cover:

1. Power transitions, zero brightness, relative brightness, invalid types,
   and adjustments while off, including repeated commands.
2. Selecting a different or current scene while off, activation failure,
   and recovery from a saved scene that cannot start.
3. Black hardware frames and previews while off, timely wake-up for slow
   scenes, and correct restoration without changing artwork.
4. Persistence round trips and loading an existing database without power.
5. Shared catalogue consistency across CLI choices, service validation, web
   metadata, and French mode mappings.
6. Fake-SDK command translation, rejected modes, phone-originated reports,
   concurrent edits, reconnect snapshots, report failures, and clean shutdown.
7. Missing/partial configuration and dependency errors with local control
   still usable; environment precedence and existing Spotify behavior.
8. HTTP power/brightness semantics and the existing controller regression suite.

Use fake hardware and cloud clients for deterministic tests. Run the existing
scene and controller tests as regression coverage. Manually inspect the phone
power control, off preview, and integration status in preview mode.

Live acceptance on the Pi and linked Google Home account checks French power,
brightness, and every configured mode, plus phone-to-cloud synchronization,
restart while off, and recovery after internet interruption. Example phrases
to try include « Allume la matrice », « Règle la luminosité de la matrice à
40 % », and « Mets la matrice en mode horloge ». Exact recognition and Google
Home's presentation of custom modes require that live check; automated tests
must not be presented as proof of it.

## Documentation and external setup

Provide setup instructions for installing the optional dependency, creating
the custom template and its three capabilities, configuring the French locale
and all mode values, copying the instance ID and credentials into `.env`,
linking Sinric Pro in Google Home, and launching the controller.

Include a concise developer guide covering where state rules live, how an
adapter uses them, how to add/register a scene, the required portal update, and
how to run tests. Never include real credentials in docs; this repository
publishes its `docs` directory through GitHub Pages.

## Sources and compatibility boundary

Consulted on 2026-10-22:

- [Sinric device templates](https://help.sinric.pro/pages/custom-templates)
  describe bundling capabilities and note Google Home device-type limitations.
- [Sinric Mode capability](https://help.sinric.pro/pages/device-templates/capabilities/mode)
  supports localized choices and maps to Google's Modes trait.
- [Sinric Brightness capability](https://help.sinric.pro/pages/device-templates/capabilities/brightness)
  maps to Google's Brightness trait.
- [Official Python SDK](https://github.com/sinricpro/python-sdk) supplies the
  cloud transport and asynchronous device integration.

During implementation planning, verify the chosen released SDK's custom-device
callbacks, event reporting, and shutdown APIs. The template must use a
Google-compatible device type that accepts Power, Brightness, and Mode. Do not
silently replace this design with multiple virtual devices if live discovery
rejects that combination; report the specific incompatibility for a focused
design adjustment.
