# Google Home through Sinric Pro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Control the matrix's power, remembered brightness, and nine scenes through French Google Home commands and the existing phone controller using one saved state.

**Architecture:** Keep decisions, persistence, and rendering in `ControlService`. Share scene metadata through a catalogue, and run an optional Sinric adapter on its own asyncio thread inside the controller process. Isolate released-SDK quirks in a small transport module; coalesce outgoing state and retain local operation through cloud failures.

**Tech Stack:** Existing Python 3.10+, Pillow, SQLite, FastAPI/Pydantic, unittest; optional `sinricpro==6.0.0`; fake hardware and SDK transports for offline tests. Retain the README's separate Python requirement for the Pi hardware package.

**Spec:** [2026-10-22-google-home-sinric-design.md](../specs/2026-10-22-google-home-sinric-design.md). Read both documents before implementation. This plan does not change the spec's review status or authorize live account changes.

## Global Constraints

- One 64 x 64 matrix, one hardware owner, one render loop, one SQLite state in `.matrix/control.db`, one controller process; single-worker deployment.
- One custom device with Power, Brightness, and Mode; documented device name `Matrice`; French locale. Individual scene settings stay in the phone controller.
- Internal scene IDs remain `clock`, `circle`, `spotify`, `weather`, `moon`, `eyes`, `walker`, `kintsugi`, `shadow`; preserve English phone labels and existing artwork.
- Persist `power`, defaulting to true for existing databases. Stored `brightness` remains the last nonzero level in 1..100; absolute zero turns off, preserving that level.
- Brightness accepts integers, excluding booleans; absolute values outside 0..100 fail. Relative changes clamp the remembered level plus delta to 0..100 under the service lock.
- Scene selection turns power on, including reselecting the current scene while off; activation failure preserves prior scene and power. Scene settings do not turn power on.
- Off means black hardware output and preview, not shutdown or paused time. Power changes wake slow rendering; saved off must not flash on startup.
- Monotonically increasing in-process revision for accepted state changes; unchanged commands do not save or publish unnecessarily. Reconnect publishes current local state, never stale history.
- Network calls never run under the service lock or on the render thread; cloud errors never stop local controls.
- Nonempty environment variables take precedence over project `.env`; preserve Spotify helper compatibility and legacy settings migration.
- Optional integration requirements; missing SDK or incomplete/invalid configuration becomes a safe integration status. Never expose secrets or signed payloads in logs, API responses, or public `docs`.
- Shutdown closes cloud command handling before the service and hardware; startup failures release opened resources.

## Review Focus

- Missing JSON fields, booleans, floats, strings, and null must not become accepted brightness/power commands through SDK defaults or Pydantic coercion (Tasks 2, 5, 7).
- A renderer that fails or waits 60 seconds must not prevent blanking, startup while off, or wake-up (Task 3).
- A phone edit during a voice callback or report must survive revision-based acknowledgement and a disconnect (Task 6).
- Partial scene startup, including a saved Spotify scene without credentials, must close partial resources and leave a usable black controller (Task 3).
- A report accepted by an SDK queue but not sent, or cancellation during connection startup, must not lose current state or leave background tasks running (Tasks 5, 6).

## SDK verification and implementation decisions

Planning inspected the published [Sinric Pro 6.0.0 wheel](https://pypi.org/project/sinricpro/6.0.0/#files), released September 6, 2026, without installing or executing it. Pin that exact version in `requirements-sinric.txt`; execution must run the offline contract tests below before calling the dependency tested. Do not use signatures from older SDK examples.

Verified in that wheel's `devices/sinric_pro_custom_device.py`, capability modules, `core/sinric_pro.py`, `core/types.py`, and `core/websocket_client.py`:

| Operation | Released API / behavior |
| --- | --- |
| Device | `SinricProCustomDevice(device_id, product_type="CUSTOM_DEVICE")`; use `"LIGHT"` for the planned light template |
| Register callbacks | `on_power_state(async_callback)` receives `bool`; `on_brightness` receives `int`; each returns `bool` |
| Relative brightness | `on_adjust_brightness` callback must return `(success: bool, brightness: int)`, despite its misleading registration annotation |
| Mode | `on_mode_state` callback receives `(mode: str, instance_id: str)` and returns `bool` |
| Events | Await `send_power_state_event(bool)`, `send_brightness_event(int)`, `send_mode_event(str, instance_id=...)`; each returns `bool`, with a 1-second per-capability limiter |
| Lifecycle | `SinricPro.get_instance()`, `add(device)`, `await begin(config)`, `await stop()`, `is_connected()`; connection callbacks are synchronous zero-argument functions |
| Configuration | `SinricProConfig(app_key=..., app_secret=..., debug=False, local_control=False, mdns=False)` avoids introducing a second local controller |

Two compatibility issues require explicit implementation, not optimistic mocks:

1. Custom-device dispatch defaults missing brightness/delta to zero and normalizes arbitrary power strings. Subclass the device and validate the raw `SinricProRequest` before delegating only the four supported actions. Reject unsupported actions and unexpected instances; use service validation for numeric ranges and state decisions.
2. Capability event success means queued, not delivered; the SDK sender creates an unawaited socket-send task and can retain old events across reconnects. In `sinric_transport.py`, attach a narrow event sender via the public `device.set_sinric_pro(...)` after `sdk.add(device)`: it delegates `get_timestamp()` to the SDK and implements `async send_message(message: dict) -> None` by checking connectivity, signing with the initialized SDK signature, and **awaiting** the captured `sdk.websocket.ws.send(...)`. It handles only this device's event messages. Retain the SDK's request responses and reconnect loop. These version-coupled attribute accesses require real-SDK contract tests; do not introduce a second WebSocket client. Socket-send completion is not a server receipt acknowledgement, so reconnect always republishes all three values.

SDK `stop()` cancels some tasks without awaiting them. The adapter's dedicated event-loop owner must cancel and gather remaining tasks before closing the loop. Disable SDK logging with `SinricProLogger.set_level(LogLevel.NONE)` and emit only application-owned, sanitized status messages.

Use stable internal IDs as configured wire mode values, with the exact French labels from the spec in the portal; explicitly validate via `MODE_TO_SCENE`. Start setup with a Light template and Power/Brightness/Mode. The [template documentation](https://help.sinric.pro/pages/custom-templates) warns that Google compatibility depends on device type; the [Mode documentation](https://help.sinric.pro/pages/device-templates/capabilities/mode) maps Mode to Google's Modes trait. **Portal acceptance and French discovery remain live acceptance gates**, not verified facts about this exact combination. If rejected, record the specific incompatibility instead of substituting multiple devices.

## File map and execution conventions

| Files | Responsibility |
| --- | --- |
| New `scene_catalogue.py`; modify `main.py`, `control_service.py` | Stable scene metadata, choices, and setting allowlists |
| Modify `control_service.py` | Persisted power, atomic commands/revisions, resilient rendering and activation |
| New `configuration.py`; modify `spotify_client.py`, `.env.example` | Neutral env lookup and integration configuration |
| New `sinric_transport.py`, `requirements-sinric.txt` | Lazy optional SDK import, raw validation, SDK compatibility and lifecycle |
| New `sinric_adapter.py` | Command translation, current-state reporting, status, dedicated loop/thread |
| Modify `web_app.py`, `web/index.html` | HTTP power and state, controller lifecycle, metadata-driven scene buttons/status |
| New `tests/test_scene_catalogue.py`, `test_configuration.py`, `test_sinric_transport.py`, `test_sinric_adapter.py` under `tests/` | Offline coverage of the new modules |
| Modify `tests/test_control_service.py`, `tests/test_web_app.py`, `tests/test_spotify.py` | Service/API changes and regressions |
| Modify `README.md`; new `docs/google-home-sinric.md`, `docs/development.md` | Installation, portal setup, acceptance checklist, extension guide |

Follow the existing `unittest` style (`IsolatedAsyncioTestCase` for async tests). Test snippets below specify assertions within fixtures using temporary stores, patched Spotify configuration, and explicit cleanup; do not access a developer's real `.env`, tokens, DB, hardware, or cloud. Use barriers/events and bounded waits instead of timing-only sleeps for races. Commit each task's explicit files only.

Before Task 1, install the existing web test dependencies with `python -m pip install -r requirements-web.txt httpx`, then run `python -m unittest discover -s tests -v` and record the baseline. Missing optional dependencies must not silently skip the final API/SDK verification runs.

### Task 1: Share the scene catalogue

**Files:** Create `scene_catalogue.py`, `tests/test_scene_catalogue.py`; modify `control_service.py` (`SCENES`, `SCENE_OPTIONS`, `state`) and `main.py` (`--scene` choices).

**Interfaces:** Produce frozen `SceneDefinition(id: str, label: str, voice_label_fr: str, icon: str, options: frozenset[str])`, `SCENE_CATALOGUE: tuple[SceneDefinition, ...]`, `SCENE_IDS: tuple[str, ...]`, `SCENE_OPTIONS: dict[str, frozenset[str]]`, and `scene_metadata() -> list[dict]`. Metadata items contain `id`, `label`, `voice_label_fr`, `icon`, `options` (sorted list). Retain service `SCENES`/`SCENE_OPTIONS` aliases for compatibility.

- [ ] **Step 1: Add failing catalogue and metadata assertions.**

  ```python
  self.assertEqual(SCENE_IDS, ("clock", "circle", "spotify", "weather", "moon", "eyes", "walker", "kintsugi", "shadow"))
  self.assertEqual([s.voice_label_fr for s in SCENE_CATALOGUE],
                   ["Horloge", "Cercle", "Spotify", "Météo", "Lune", "Yeux", "Marcheur", "Kintsugi", "Ombre"])
  self.assertEqual(service.state()["scene_metadata"], scene_metadata())
  self.assertEqual(tuple(service.state()["scenes"]), SCENE_IDS)
  ```

  Add `test_cli_choices_match_catalogue` using `main.py --help` (no GUI); assert all IDs appear in order in the `--scene` choice group. Assert metadata exactly preserves the current `sceneNames` English labels/icons and service option sets, including empty options for circle/moon/walker.
- [ ] **Step 2:** Run `python -m unittest discover -s tests -p test_scene_catalogue.py -v`; expect missing module/metadata failures.
- [ ] **Step 3:** Implement the catalogue, derive CLI/service constants, and add metadata to `state()` without deleting `scenes`. Keep renderer dispatch in `HeadlessScene` and the desktop emulator.
- [ ] **Step 4:** Repeat Step 2 and the existing control-service tests; expect all pass.
- [ ] **Step 5:** Stage the four task files and commit `refactor: share scene catalogue across controller and CLI`.

### Task 2: Add persisted power and atomic state commands

**Files:** Modify `control_service.py` (`ControlSettings`, `SettingsStore`, mutation methods, `state`); extend `tests/test_control_service.py`.

**Interfaces:** Add `set_power(power: bool) -> dict`, `adjust_brightness(delta: int) -> dict`; retain `set_brightness(brightness: int) -> dict`, `select_scene(scene: str) -> dict`, and other existing signatures. Every returned state is captured under the same lock as the mutation and includes `power: bool`, `revision: int`. Brightness command results additionally include `applied_brightness: int` (the absolute/clamped command target, 0..100); this result-only field is neither persisted nor included in `state()`. It lets relative commands acknowledge zero while `brightness` retains the remembered value. Revision starts at zero per process and advances once per changed settings transaction; never persist it.

- [ ] **Step 1: Write failing command/persistence tests**, including this sequence:

  ```python
  self.assertEqual(service.set_brightness(40)["brightness"], 40)
  off = service.set_brightness(0)
  self.assertEqual((off["power"], off["brightness"]), (False, 40))
  self.assertEqual(service.set_brightness(0)["revision"], off["revision"])
  self.assertEqual(service.adjust_brightness(10)["brightness"], 50)
  self.assertFalse(service.state()["power"])
  self.assertEqual(service.adjust_brightness(500)["brightness"], 100)
  zero = service.adjust_brightness(-500)
  self.assertEqual((zero["applied_brightness"], zero["brightness"]), (0, 100))
  self.assertFalse(service.state()["power"])
  self.assertTrue(service.set_power(True)["power"])
  ```

  `test_strict_command_types` rejects `True`, `False`, `1.5`, `"40"`, `None` for both brightness methods, and absolute -1/101; power rejects all non-booleans. Each rejection preserves settings, revision, and store save count. `test_noop_does_not_save` covers repeated power/brightness/scene-options/GPIO commands. `test_legacy_and_power_round_trip` loads a JSON settings row without power, verifies true plus existing eye migration, then saves/reloads off/40/moon. `test_relative_changes_are_atomic` runs two barrier-started +10 adjustments from 40 and asserts 60 with two distinct revisions. Scene options while off retain off.
- [ ] **Step 2:** Run `python -m unittest discover -s tests -p test_control_service.py -v`; expect new behavior failures.
- [ ] **Step 3:** Add `ControlSettings.power: bool = True`. Centralize changed-settings save/revision updates under the existing `RLock`, capturing command results before releasing it. Preserve no-op behavior across all settings mutations, including Spotify client-ID updates. Implement integer validation before comparisons and locked relative read/modify/write. Hardware brightness always receives 1..100; power never calls brightness zero.
- [ ] **Step 4:** Repeat Step 2; expect pass, including legacy persistence and Spotify regression behavior. Spy on the store to distinguish changed settings from unchanged requests.
- [ ] **Step 5:** Commit the two files as `feat: persist power and unify brightness commands`.

### Task 3: Make scene activation and display blanking resilient

**Files:** Modify `control_service.py` (initialization, `_replace_source`, render loop, frame preview, scene selection, shutdown, active-source callers); extend `tests/test_control_service.py`. Preserve `headless_scene.py` artwork and worker lifecycle.

**Interfaces:** `source: HeadlessScene | None` supports a failed saved scene. `state()` adds `scene_error: str | None`; `select_scene` retains its signature and atomically commits scene plus power=true after successful preparation. Private `_render_once() -> float` renders/blanks one iteration and returns the wait interval for deterministic testing.

- [ ] **Step 1: Add failing fake-output/fake-source tests.** `test_off_blanks_even_if_renderer_raises` asserts `frame_png()` decodes to `getbbox() is None`, last hardware frame is black, and no call to the failing source is needed while off. `test_saved_off_never_flashes` inspects **all** output frames during startup, not just the final frame. `test_moon_power_wakes` uses a fake source with `interval=60`, waits for the first frame, toggles off/on, and asserts output events each arrive within one second and artwork bytes return unchanged.

  ```python
  service.set_power(False)
  self.assertTrue(service.select_scene(service.state()["scene"])["power"])
  service.set_power(False)
  before = service.state()
  with self.assertRaises(SpotifyError):
      service.select_scene("spotify")  # fixture has no configured client ID
  self.assertEqual(service.state()["scene"], before["scene"])
  self.assertFalse(service.state()["power"])
  self.assertEqual(service.state()["revision"], before["revision"])
  ```

  `test_failed_saved_scene_recovers` saves Spotify without credentials, constructs/starts the service, asserts saved ID retained, black preview, actionable `scene_error`, and successful recovery to circle. Retry the same saved ID after fixing the fake source and assert it activates. `test_partial_source_and_startup_cleanup` asserts failed replacements close their partial worker, preserve old source, and initialization failure after hardware allocation closes output. Test rapid off/on and same-frame scenes so cached bytes cannot suppress restoration.
- [ ] **Step 2:** Run the control-service test command; expect these new tests to fail.
- [ ] **Step 3:** Initialize locks/events/black preview before fallible startup work. Prepare replacement sources before changing saved state; close failed candidates. Handle source-start failure at startup by retaining settings, setting `source=None` and a safe error, and staying reachable. Keep active-source options and Spotify account methods safe when source is absent. Reselecting a healthy current scene only changes power; reselecting a failed one retries activation.
- [ ] **Step 4:** Implement `_render_once` with off/source-unavailable blanking before source rendering. Clear comparisons on transitions, expose black preview immediately when off is accepted, and wake output promptly. Clear the wake event under the lock **before** rendering so a command between rendering and waiting cannot lose its wake. Ensure time-based scenes show current time on return and data workers remain owned until normal cleanup. Make `stop()` safe before `start()` and after partial initialization.
- [ ] **Step 5:** Run service tests plus `python -m unittest discover -s tests -p test_hardware_output.py -v`; expect pass without physical hardware.
- [ ] **Step 6:** Commit `fix: blank powered-off displays and recover scene startup failures`.

### Task 4: Extract shared environment configuration

**Files:** Create `configuration.py`, `tests/test_configuration.py`; modify `spotify_client.py`, `.env.example`, and relevant assertions in `tests/test_spotify.py`.

**Interfaces:** `dotenv_value(name: str, path: Path | None = None) -> str`, `configured_value(name: str, path: Path | None = None) -> str`; frozen `SinricSettings(app_key: str, app_secret: str, device_id: str, scene_instance_id: str)` with secret fields excluded from repr; `load_sinric_settings(path: Path | None = None) -> SinricSettings | None`, raising `ValueError` with a safe actionable message on partial/invalid config.

- [ ] **Step 1: Write failing env-resolution/config tests.** With a temporary file containing `EXAMPLE='from file'`, assert `configured_value("EXAMPLE", path) == "from environment"` under a nonempty environment override and `== "from file"` for an empty/whitespace override. Cover quoted values, missing/unreadable file, no code execution, and unchanged Spotify helper patch points. Assert no four Sinric values means `None`, any proper subset raises naming the missing variable only, and complete placeholder-shaped test credentials produce the four exact fields. Validate UUID app key, secret length >=32, 24-hex device ID, and nonempty instance ID without echoing values.
- [ ] **Step 2:** Run `python -m unittest discover -s tests -p test_configuration.py -v`; expect missing-module failures.
- [ ] **Step 3:** Move parsing to the neutral module. Retain `spotify_client.ENV_FILE` and `dotenv_value` compatibility, forwarding its effective path; preserve existing tests that patch the wrapper. Both Spotify environment readers keep nonempty-environment-first behavior. Add the four spec variables to `.env.example` as commented placeholders so copying it alone does not enable Sinric; clarify the existing Spotify-only warning about secrets.
- [ ] **Step 4:** Run configuration tests and `python -m unittest discover -s tests -p 'test_spotify*.py' -v`; expect pass. Assert configuration repr/error messages contain neither test key nor secret.
- [ ] **Step 5:** Commit `refactor: share environment configuration for optional integrations`.

### Task 5: Wrap the pinned Sinric SDK with an offline contract

**Files:** Create `sinric_transport.py`, `requirements-sinric.txt`, `tests/test_sinric_transport.py`.

**Interfaces:** SDK-free `SinricCallbacks` dataclass has async `power(bool)->bool`, `brightness(int)->bool`, `adjust_brightness(int)->tuple[bool,int]`, `mode(str,str)->bool`, and sync `connected()->None`, `disconnected()->None`. `SinricTransport(settings: SinricSettings, callbacks: SinricCallbacks)` exposes `async start() -> None`, `async close() -> None`, `is_connected() -> bool`, `async report_power(bool) -> bool`, `async report_brightness(int) -> bool`, `async report_mode(str) -> bool`. SDK imports happen only when starting a configured transport. The instance ID comes from settings.

- [ ] **Step 1: Write failing real-SDK contract tests with patched network.** Use the actual pinned classes and synthetic `SinricProRequest` objects; replace connection/socket operations with async fakes. `test_raw_validation_precedes_sdk_defaults` passes `{}`, wrong types, unknown action, invalid power strings, and wrong/missing Mode instance; assert false and zero service callback invocations. Valid power wire values are exactly `On`/`Off`; numeric requests require their field and `type(value) is int`. For actions without Mode reject nonempty instance IDs.

  ```python
  self.assertFalse(await device.handle_request(request("setBrightness", {})))
  self.assertFalse(await device.handle_request(request("setBrightness", {"brightness": True})))
  self.assertTrue(await device.handle_request(request("adjustBrightness", {"brightnessDelta": -20})))
  self.assertEqual(last_request.response_value, {"brightness": 20})
  ```

  The fixture's `request` helper constructs `SinricProRequest`; its `last_request` references that object. Set the relative callback result to `(True, 20)`. Additional tests inspect signed event payloads with dummy credentials (never log them), correct Mode instance, rate-limit false results, and real `begin`/`stop` calls. `test_report_waits_for_send_and_propagates_failure` blocks then fails fake socket send and asserts report cannot succeed early. `test_events_never_enter_sdk_send_queue` asserts no queued historical event after disconnect. Contract tests are optional only in the base dependency lane, mandatory here.
- [ ] **Step 2:** Add `sinricpro==6.0.0` to the new requirements file and install with `python -m pip install -r requirements-sinric.txt`; run `python -m unittest discover -s tests -p test_sinric_transport.py -v`; expect wrapper-related failures, not import skips.
- [ ] **Step 3:** Implement the validated custom-device subclass, lazy SDK construction, callbacks, event sender, and lifecycle from the verification section. Register device/callbacks before `begin`; turn off SDK logs and LAN/mDNS. Scope SDK ownership to this single adapter; guard singleton lifecycle against duplicate registrations. Convert exceptions to safe failure signals without logging raw SDK text, config, or requests. `close()` must also clean up a partially started SDK.
- [ ] **Step 4:** Repeat contract tests; expect pass with no network. Verify importing `sinric_transport` with SDK imports blocked still works; only configured startup reports a missing optional dependency.
- [ ] **Step 5:** Commit `feat: add validated Sinric SDK transport`.

### Task 6: Translate commands and synchronize the newest state

**Files:** Create `sinric_adapter.py`, `tests/test_sinric_adapter.py`.

**Interfaces:** `MODE_TO_SCENE: dict[str,str]` explicitly derives the configured ID-to-ID map from `SCENE_IDS`. `SinricAdapter(service: ControlService, settings: SinricSettings | None, *, transport_factory=SinricTransport, configuration_error: str = "")` exposes `start() -> None`, `stop() -> None`, `status() -> dict` returning `{state, message}`. States are exactly `disabled`, `connecting`, `connected`, `error`. A sanitized configuration error with no settings produces error status and no thread. Async callback methods match `SinricCallbacks`; private `async _sync_once() -> None` enables deterministic tests. No SDK types leak into the service.

- [ ] **Step 1: Add failing fake-transport command tests.** Assert power/brightness/relative calls use service methods; all nine configured modes map to their IDs; unknown mode/wrong instance fail without mutation; activation failure returns false. Verify zero acknowledges brightness zero, preserves remembered brightness, and reports the resulting off state. For relative adjustment return `(True, result["applied_brightness"])`, including zero when it turns off, rather than re-reading a possibly changed state. A mode command while off acknowledges mode and separately reports power=true.
- [ ] **Step 2: Add failing synchronization and ownership tests.**

  ```python
  # Fixtures run one sync cycle at a time against a fake connected transport.
  service.set_brightness(20)
  service.set_brightness(40)
  await adapter._sync_once()
  self.assertEqual(transport.brightness_reports, [40])
  await adapter._sync_once()
  self.assertEqual(transport.brightness_reports, [40])
  ```

  `test_phone_edit_during_voice_ack_is_reported` pauses after voice accepts 40 at revision N, applies phone 70 at N+1, resumes acknowledgement, then asserts 70 is reported. `test_report_failure_uses_newest_state` fails 40, changes to 80, then asserts retry is 80. `test_reconnect_snapshot` makes many offline edits, reconnects, and asserts exactly the latest power/brightness/mode are scheduled, no edit history. `test_inflight_old_report_cannot_ack_new_edit` blocks send(40), changes to 70, completes 40, and asserts 70 still reports. Test repeated commands, settings-only revisions, rate-limited false results, wrong-instance rejection, no report-to-command feedback, and local control while connection/report is blocked.

  Ownership tests assert stop rejects new callbacks, joins the thread, gathers pending tasks, and handles cancellation during `begin`. Error/status tests inject exceptions containing dummy secrets and signed payload text and assert these strings are absent from logs and status.
- [ ] **Step 3:** Run `python -m unittest discover -s tests -p test_sinric_adapter.py -v`; expect new adapter failures.
- [ ] **Step 4:** Implement callbacks delegating to the service, retaining the returned revision/snapshot. Use one asyncio lock around command acknowledgement bookkeeping and report cycles; HTTP edits remain independent under the service lock. Offload potentially blocking service calls with `asyncio.to_thread`. Track acknowledgement **per field and returned revision/value**, never a global “ignore next change” flag. Suppress only the field/value actually acknowledged by that command: scene-on and zero-off side effects still report. Brightness reports use remembered 1..100 alongside power; a wire zero acknowledgement is not acknowledgement of the remembered level.
- [ ] **Step 5:** Implement a 100 ms snapshot poll with O(1) latest-state storage, no event-history queue. Report only differing fields, at most once per second per field; false/exception leaves that field dirty for retry using a fresh snapshot. Capture a snapshot/revision before sending; completion only acknowledges that captured value. Connection callbacks invalidate all report acknowledgements and request a fresh snapshot. Do not report while disconnected or apply cloud snapshots locally; let the SDK reconnect. Own all async work in one named thread using an asyncio runner; on stop reject commands, close transport, cancel/gather tasks, then join. Translate absent config to disabled, startup/report failure to safe error, reconnect attempts to connecting, and recovery to connected.
- [ ] **Step 6:** Repeat adapter tests and transport tests; expect pass. Check an actual thread shutdown in addition to single-cycle async tests.
- [ ] **Step 7:** Commit `feat: synchronize matrix state with Sinric commands and events`.

### Task 7: Connect HTTP, phone UI, and integration lifecycle

**Files:** Modify `web_app.py`, `web/index.html`, `tests/test_web_app.py`.

**Interfaces:** `create_app(service: ControlService, integration: SinricAdapter | None = None) -> FastAPI` retains existing injected-service behavior. Add `PowerChange(power: StrictBool)` and `PUT /api/power`; use `StrictInt` for brightness. All state-bearing HTTP responses retain service fields and add `integration: {state: str, message: str}` through one response helper. Missing integration returns disabled. Lifecycle remains owned by `main()`, not duplicated in FastAPI startup hooks.

- [ ] **Step 1: Write failing HTTP assertions.**

  ```python
  off = client.put("/api/power", json={"power": False})
  self.assertEqual(off.status_code, 200)
  self.assertFalse(off.json()["power"])
  self.assertEqual(client.put("/api/brightness", json={"brightness": 0}).status_code, 200)
  self.assertEqual(client.put("/api/brightness", json={"brightness": True}).status_code, 422)
  self.assertEqual(client.put("/api/brightness", json={"brightness": 101}).status_code, 400)
  self.assertEqual(client.put("/api/scene", json={"scene": "circle"}).json()["power"], True)
  ```

  Test missing/null/float/string fields (422), malformed JSON (422), invalid power types, failure to activate Spotify (409 and unchanged state), black PNG while off, and preserved remembered level/legacy fields. Assert metadata, revision, `scene_error`, and integration status are present without credentials. Keep existing scene-settings and Spotify flow tests.
- [ ] **Step 2:** Add lifecycle tests with fake service/adapter/uvicorn: `test_main_shutdown_orders_adapter_before_service`, `test_partial_config_keeps_http_running`, `test_missing_sdk_keeps_http_running`, `test_invalid_credentials_keep_http_running`, and `test_startup_failure_closes_open_resources`. Assert calls end `adapter.stop`, then `service.stop`; cleanup still reaches service if adapter shutdown raises. Patch env/file access so tests cannot load real configuration.
- [ ] **Step 3:** Run `python -m unittest discover -s tests -p test_web_app.py -v`; expect new endpoint/lifecycle failures.
- [ ] **Step 4:** Implement strict request models and power endpoint. Wrap all resources opened by `main` in cleanup from their point of creation; start service, load integration config, start adapter, run single-worker uvicorn. Configuration exceptions create `SinricAdapter(service, None, configuration_error=safe_message)` with no network thread; local startup continues. Use the response helper for both GET state and mutation responses so status does not disappear after commands.
- [ ] **Step 5:** Replace `sceneNames`/`makeScenes(names)` with metadata-driven buttons, preserving labels/icons and existing settings panels. Add accessible power button (`aria-pressed`) and explicit On/Off text; keep brightness slider 1..100 and remembered value visible while off. Add a separate compact Sinric status/message; cloud failure must not change the local Connected indicator. Preserve off preview and current-scene reselect behavior.
- [ ] **Step 6:** Run all web tests without skips. Run `python web_app.py` in preview mode and inspect a phone-sized viewport: power toggles, remembered slider, black preview, all nine labels, current scene turning on, and disabled/error status while local controls work. Use a temporary configuration/store or isolated checkout for this check; stop the server afterward. Record actual observations, not just HTML string checks.
- [ ] **Step 7:** Commit `feat: expose power and Sinric status in phone controller`.

### Task 8: Document setup and complete acceptance evidence

**Files:** Modify `README.md`; create `docs/google-home-sinric.md`, `docs/development.md`. Reuse the tests from Tasks 1–7; do not add a parallel test framework.

**Interfaces:** Setup guide is the operational contract for the four environment variables, ID wire values, French labels, optional installation, and one-process startup. Developer guide identifies the shared state/catalogue boundaries and required cloud-template updates.

- [ ] **Step 1:** Write setup instructions: install `requirements-web.txt` and `requirements-sinric.txt`; create a Google-compatible Light custom template with exactly Power, Brightness, Mode; configure French locale, `Matrice`, all nine ID/label pairs, and copy the exact Mode instance ID. Explain custom-template capability/license limits by linking the official portal docs. Add only placeholders for credentials, link Sinric Pro in Google Home, refresh/resync devices, and start the existing controller (including current Pi/systemd guidance). Explain outbound connectivity with no port forwarding, disabled/partial/missing-SDK states, and restarting after configuration changes.
- [ ] **Step 2:** Write the developer guide: service owns decisions; adapter translates; add renderer behavior in `HeadlessScene` and any desktop/snapshot paths, metadata/allowlist in catalogue, then portal modes and Google refresh. Catalogue registration alone neither implements artwork nor updates the cloud. Document the SDK pin/compatibility tests and offline commands.
- [ ] **Step 3:** Run `python -m unittest discover -s tests -v` with web, httpx, and Sinric dependencies installed; expect all tests pass and no optional API/SDK skips. In a separate base-only environment run the suite again; only explicitly optional API/real-SDK tests may skip, while service, configuration, and fake-adapter tests pass. Run `python main.py --scene circle --snapshot .preview/sinric-circle.png` to confirm standalone rendering remains independent of Sinric. Run `git diff --check`; expect no whitespace errors.
- [ ] **Step 4:** Add a dated acceptance checklist to the setup guide, marking each live check pending until performed on the Pi/linked account: French on/off, absolute and relative brightness including zero, each of nine modes, phone-to-cloud reports, failed scene activation, restart while off without flash, and internet loss/recovery with local controls working. Try « Allume la matrice », « Règle la luminosité de la matrice à 40 % », « Mets la matrice en mode horloge ». Record device type/template locale/SDK version, actual recognized phrases, and observations. If Light plus the three capabilities fails discovery, record the exact incompatibility for a focused design adjustment; do not create substitute devices.
- [ ] **Step 5:** Review docs and staged diff for real credentials, confirm all eight spec verification categories have evidence or an explicit pending live check, then commit `docs: explain Sinric setup development and acceptance checks`.

## Completion boundaries

Tasks 1–8 provide offline implementation and documentation. Live French recognition and Google Home presentation require the user's Pi and linked account; automated tests cannot establish them. Report offline results and outstanding live checks separately. The present change is this implementation plan only; implementation starts after its review.
