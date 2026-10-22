"""Translate cloud commands and report only the newest shared service state."""
import asyncio
from threading import Event, RLock, Thread
from time import monotonic

from configuration import SinricSettings
from scene_catalogue import SCENE_IDS
from sinric_transport import SinricCallbacks, SinricTransport

MODE_TO_SCENE = {scene: scene for scene in SCENE_IDS}


class SinricAdapter:
    def __init__(self, service, settings: SinricSettings | None, *, transport_factory=SinricTransport,
                 configuration_error: str = ''):
        self.service, self.settings = service, settings
        self.transport_factory = transport_factory
        self.transport = None
        self.thread = None
        self.loop = self.task = None
        self.ready = Event()
        self.stopping = Event()
        self.status_lock = RLock()
        self.sync_lock = asyncio.Lock()
        self.acknowledged = {}  # field -> (revision, value), bounded to three fields
        self.last_attempt = {}
        self.generation = 0
        self._state = 'error' if configuration_error else ('connecting' if settings else 'disabled')
        self._message = ('Complete all four SINRIC settings in .env and restart.' if configuration_error
                         else ('Connecting to Sinric.' if settings else 'Sinric is not configured.'))
        self.callbacks = SinricCallbacks(self._power, self._brightness, self._adjust_brightness,
                                        self._mode, self._connected, self._disconnected)

    def status(self) -> dict:
        with self.status_lock:
            return dict(state=self._state, message=self._message)

    def _status(self, state, message):
        with self.status_lock:
            self._state, self._message = state, message

    def _connected(self):
        self.generation += 1
        self.acknowledged.clear()
        self.last_attempt.clear()
        self._status('connected', 'Connected to Sinric.')

    def _disconnected(self):
        self.generation += 1
        self.acknowledged.clear()
        self.last_attempt.clear()
        self._status('connecting', 'Sinric disconnected; reconnecting. Local controls remain available.')

    async def _command(self, method, value, field):
        if self.stopping.is_set() or self.settings is None:
            return None
        async with self.sync_lock:
            if self.stopping.is_set():
                return None
            generation = self.generation
            try:
                result = await asyncio.to_thread(getattr(self.service, method), value)
            except Exception:
                return None
            if generation == self.generation:
                # Zero acknowledges a wire value of zero, not the remembered brightness.
                if field != 'brightness' or result['applied_brightness'] != 0:
                    self.acknowledged[field] = (result['revision'], result[field])
                else:
                    self.acknowledged.pop('brightness', None)
            return result

    async def _power(self, power):
        return await self._command('set_power', power, 'power') is not None

    async def _brightness(self, value):
        return await self._command('set_brightness', value, 'brightness') is not None

    async def _adjust_brightness(self, delta):
        result = await self._command('adjust_brightness', delta, 'brightness')
        return (False, 0) if result is None else (True, result['applied_brightness'])

    async def _mode(self, mode, instance):
        if self.settings is None or instance != self.settings.scene_instance_id or type(mode) is not str or mode not in MODE_TO_SCENE:
            return False
        return await self._command('select_scene', MODE_TO_SCENE[mode], 'scene') is not None

    async def _sync_once(self) -> None:
        if self.stopping.is_set() or self.transport is None or not self.transport.is_connected():
            return
        async with self.sync_lock:
            snapshot = await asyncio.to_thread(self.service.state)
            generation = self.generation
            failed = False
            for field, report in (('power', self.transport.report_power),
                                  ('brightness', self.transport.report_brightness),
                                  ('scene', self.transport.report_mode)):
                if self.stopping.is_set() or not self.transport.is_connected() or generation != self.generation:
                    return
                revision, value = snapshot['revision'], snapshot[field]
                acknowledged = self.acknowledged.get(field)
                if acknowledged is not None and acknowledged[0] <= revision and acknowledged[1] == value:
                    continue
                now = monotonic()
                if now - self.last_attempt.get(field, float('-inf')) < 1:
                    failed = True
                    continue
                self.last_attempt[field] = now
                # A failed send may still reach the peer. Its preceding value is
                # no longer a safe acknowledgement if local state returns to it.
                self.acknowledged.pop(field, None)
                try:
                    success = await report(value)
                except Exception:
                    success = False
                if success and generation == self.generation:
                    self.acknowledged[field] = (revision, value)
                else:
                    failed = True
            if generation == self.generation:
                if failed:
                    self._status('error', 'Sinric report pending; check internet access and credentials. Retrying current state.')
                else:
                    self._status('connected', 'Connected to Sinric.')

    def start(self) -> None:
        if self.settings is None or self.thread is not None or self.stopping.is_set():
            return
        self.thread = Thread(target=self._thread_main, name='matrix-sinric', daemon=True)
        self.thread.start()

    def _thread_main(self):
        # asyncio.run (also on Python 3.10) gathers tasks and drains to_thread calls.
        try:
            asyncio.run(self._owned_run())
        except asyncio.CancelledError:
            pass
        finally:
            self.loop = None

    async def _owned_run(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.create_task(self._run())
        self.ready.set()
        await self.task

    async def _run(self):
        try:
            self.transport = self.transport_factory(self.settings, self.callbacks)
            await self.transport.start()
            while not self.stopping.is_set():
                await self._sync_once()
                await asyncio.sleep(.1)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._status('error', 'Sinric unavailable. Install requirements-sinric.txt, check credentials and internet, then restart.')
        finally:
            if self.transport is not None:
                try:
                    await self.transport.close()
                except Exception:
                    self._status('error', 'Sinric shutdown failed. Restart the controller.')

    def stop(self) -> None:
        self.stopping.set()
        if self.thread is None:
            return
        self.ready.wait()
        loop = self.loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass  # Owner closed between the check and cancellation.
        self.thread.join()
