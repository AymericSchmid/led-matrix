# Controller development

`ControlService` owns validation, persistence, power rules, scene activation, and
the render loop. All adapters use its commands and atomic returned snapshots.
`revision` increases once per changed settings transaction, starts at zero per
process, and is not persisted. No-op commands do not save. Brightness command
results include `applied_brightness`; ordinary state omits it, retaining the last
nonzero `brightness` alongside independent `power`.

HTTP in `web_app.py` adds strict request models and integration status.
`sinric_adapter.py` translates only configured Power/Brightness/Mode commands and
owns its asyncio thread. It polls snapshots every 100ms, tracks three field/value
acknowledgements with revisions, and retries the latest values at most once per
second per field. Commands acknowledge only their own fields: selecting a mode
also turning on, or brightness zero turning off, still reports the side effect.
Network calls stay outside the service lock. On shutdown stop the adapter before
the service/hardware; its event-loop owner drains cancellation and worker tasks.

## Add a scene

1. Implement renderer behavior and lifecycle in the scene module and
   `HeadlessScene`. Keep frames 64×64 RGB. Add the desktop/emulator and snapshot
   dispatch paths in `main.py` as required; catalogue registration does not create
   artwork or automatically register renderer code.
2. Register stable ID, existing English label, French voice label, icon and setting
   allowlist in `scene_catalogue.py`. CLI choices, service validation, HTTP metadata,
   phone buttons, and `MODE_TO_SCENE` derive from it. IDs are persistence/API keys;
   avoid renaming them without migration. Add setting fields and validation to
   the service plus relevant phone settings UI.
3. Add the wire ID and localized label manually to the Sinric template, then save
   and refresh/resync Google Home. Local catalogue changes cannot modify the cloud
   template. Repeat live recognition/discovery checks in the setup guide.
4. Add behavioral tests using temporary stores and fake network/hardware boundaries.
   Test activation failure and cleanup, power-off behavior, and restart persistence.

## SDK compatibility boundary

`sinric_transport.py` lazily imports **sinricpro 6.0.0**. It validates raw request
fields before SDK defaults/coercion and supports only four actions. The custom
device remains `LIGHT`, with one exact Mode instance and ID-to-ID scene mapping.

SDK event success ordinarily means queued. Our device-specific sender uses public
`set_sinric_pro` with a narrow sender that checks the device/event, signs through
the initialized SDK signature, captures and awaits `sdk.websocket.ws.send`, and
checks connection identity afterward. SDK request-response routing and reconnect
remain intact; there is no second socket. This is version-coupled and socket-send
completion is not server acknowledgement. The exclusive owner resets the pinned
SDK singleton on close so stale callbacks, devices, or loop-bound locks cannot
leak into another controller. SDK logging is disabled; status messages are owned
by the app and never include raw exception text, keys or signed payloads.

Before changing the pin, inspect the release source and run the real-SDK contract
tests with network patched. Check dispatch, relative response tuples, signing over
exact transmitted bytes, limiter results, queue avoidance, reconnect, cancellation
during begin, and stop. Keep SDK types out of the service and adapter.

## Offline checks

```sh
python -m pip install -r requirements-web.txt -r requirements-sinric.txt httpx
python -m unittest discover -s tests -v
python main.py --scene circle --snapshot .preview/sinric-circle.png
git diff --check
```

The full lane must run HTTP and real SDK tests without skips. A separate base
environment with only `requirements.txt` may skip explicitly optional HTTP and
real-SDK tests; service/configuration/fake-adapter tests still run. Test fixtures
must not load developer credentials or modify real tokens, stores or hardware.
See [Google Home setup and pending acceptance](google-home-sinric.md).
