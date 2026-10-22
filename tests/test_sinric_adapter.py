import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from configuration import SinricSettings
from control_service import ControlService, SettingsStore
from sinric_adapter import SinricAdapter, MODE_TO_SCENE

SETTINGS = SinricSettings('12345678-1234-1234-1234-123456789abc', 's' * 32, 'a' * 24, 'scene')


class FakeTransport:
    def __init__(self, settings, callbacks):
        self.callbacks = callbacks
        self.connected = True
        self.reports = []
        self.failure = False
        self.block = None
        self.entered = None
        self.started, self.closed = Event(), Event()

    async def start(self):
        self.started.set()
        self.callbacks.connected()

    async def close(self):
        self.closed.set()

    def is_connected(self):
        return self.connected

    async def report(self, field, value):
        self.reports.append((field, value))
        if self.entered:
            self.entered.set()
        if self.block:
            await self.block.wait()
        if self.failure:
            raise OSError('secret signed payload')
        return True

    async def report_power(self, value): return await self.report('power', value)
    async def report_brightness(self, value): return await self.report('brightness', value)
    async def report_mode(self, value): return await self.report('scene', value)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch('control_service.configured_env_client_id', return_value=''):
            self.service = ControlService(store=SettingsStore(Path(directory.name) / 'state.db'))
        self.addCleanup(self.service.stop)
        self.adapter = SinricAdapter(self.service, SETTINGS, transport_factory=FakeTransport)
        self.transport = FakeTransport(SETTINGS, self.adapter.callbacks)
        self.adapter.transport = self.transport

    async def cycle(self):
        # Advance the clock deterministically beyond capability rate limits.
        self.now = getattr(self, 'now', time.monotonic()) + 2
        with patch('sinric_adapter.monotonic', return_value=self.now):
            await self.adapter._sync_once()

    async def test_translation_and_side_effect_reports(self):
        await self.cycle()
        self.transport.reports.clear()
        self.assertTrue(await self.adapter.callbacks.brightness(40))
        await self.cycle()
        self.assertEqual(self.transport.reports, [])
        self.assertTrue(await self.adapter.callbacks.brightness(0))
        self.assertEqual(self.service.state()['brightness'], 40)
        await self.cycle()
        self.assertIn(('power', False), self.transport.reports)
        self.assertTrue(await self.adapter.callbacks.mode('circle', 'scene'))
        self.transport.reports.clear()
        await self.cycle()
        self.assertEqual(self.transport.reports, [('power', True)])
        self.assertEqual(await self.adapter.callbacks.adjust_brightness(-500), (True, 0))
        self.assertEqual(await self.adapter.callbacks.adjust_brightness(500), (True, 100))
        self.assertFalse(self.service.state()['power'])

    async def test_all_modes_and_rejections(self):
        for name in ('clock', 'circle', 'spotify', 'weather', 'moon', 'eyes', 'walker', 'kintsugi', 'shadow'):
            with patch.object(self.service, '_new_source', return_value=Mock()):
                self.assertTrue(await self.adapter.callbacks.mode(name, 'scene'))
                self.assertEqual(self.service.state()['scene'], name)
        before = self.service.state()
        for mode, instance in [('unknown', 'scene'), ('clock', 'wrong'), ('clock', '')]:
            self.assertFalse(await self.adapter.callbacks.mode(mode, instance))
            self.assertEqual(self.service.state(), before)
        with patch.object(self.service, '_new_source', side_effect=RuntimeError('secret')):
            self.assertFalse(await self.adapter.callbacks.mode('circle', 'scene'))
        self.assertEqual(self.service.state(), before)
        for value in (True, '40', None, 1.5, -1, 101):
            self.assertFalse(await self.adapter.callbacks.brightness(value))

    async def test_coalescing_noops_and_settings_only(self):
        await self.cycle()
        self.transport.reports.clear()
        self.service.set_brightness(20)
        self.service.set_brightness(40)
        await self.cycle()
        await self.cycle()
        self.service.set_scene_options('clock', {'theme': 'dark'})
        await self.cycle()
        self.assertEqual(self.transport.reports, [('brightness', 40)])

    async def test_phone_edit_during_voice_ack_is_reported(self):
        await self.cycle()
        self.transport.reports.clear()
        original = self.service.set_brightness
        def command(value):
            result = original(value)
            original(70)  # Phone command after atomic voice result, before acknowledgement.
            return result
        with patch.object(self.service, 'set_brightness', side_effect=command):
            self.assertTrue(await self.adapter.callbacks.brightness(40))
        await self.cycle()
        self.assertEqual(self.transport.reports, [('brightness', 70)])

    async def test_report_failure_uses_newest_state(self):
        await self.cycle()
        self.transport.reports.clear()
        self.transport.failure = True
        self.service.set_brightness(40)
        await self.cycle()
        self.assertEqual(self.adapter.status()['state'], 'error')
        self.assertNotIn('secret', str(self.adapter.status()))
        self.transport.failure = False
        self.service.set_brightness(80)
        await self.cycle()
        self.assertEqual(self.transport.reports, [('brightness', 40), ('brightness', 80)])
        self.assertEqual(self.adapter.status()['state'], 'connected')

    async def test_reconnect_snapshot(self):
        await self.cycle()
        self.transport.reports.clear()
        self.transport.connected = False
        self.adapter.callbacks.disconnected()
        for value in (20, 30, 40):
            self.service.set_brightness(value)
        self.service.set_power(False)
        await self.cycle()
        self.assertEqual(self.transport.reports, [])
        self.transport.connected = True
        self.adapter.callbacks.connected()
        await self.cycle()
        self.assertEqual(self.transport.reports, [('power', False), ('brightness', 40), ('scene', 'clock')])

    async def test_inflight_old_report_cannot_ack_new_edit(self):
        await self.cycle()
        self.transport.reports.clear()
        self.service.set_brightness(40)
        entered, release = asyncio.Event(), asyncio.Event()
        self.transport.entered, self.transport.block = entered, release
        task = asyncio.create_task(self.cycle())
        await asyncio.wait_for(entered.wait(), 1)
        # This completes while the cloud send is blocked.
        self.service.set_brightness(70)
        self.service.set_power(False)
        release.set()
        await task
        await self.cycle()
        self.assertIn(('brightness', 70), self.transport.reports)
        self.assertIn(('power', False), self.transport.reports)

    async def test_false_report_retries_and_reconnect_during_send(self):
        await self.cycle()
        self.service.set_brightness(40)
        with patch.object(self.transport, 'report_brightness', return_value=False):
            await self.cycle()
        self.service.set_brightness(50)
        await self.cycle()
        self.assertIn(('brightness', 50), self.transport.reports)
        self.transport.reports.clear()
        self.service.set_brightness(60)
        original = self.transport.report_brightness
        async def report(value):
            self.adapter.callbacks.disconnected()
            self.adapter.callbacks.connected()
            return await original(value)
        with patch.object(self.transport, 'report_brightness', side_effect=report):
            await self.cycle()
        await self.cycle()
        self.assertEqual([r for r in self.transport.reports if r[0] == 'brightness'], [('brightness', 60), ('brightness', 60)])


class OwnershipTests(unittest.TestCase):
    def test_owner_works_without_python_311_runner(self):
        # Python 3.10 has asyncio.run, but no Runner class.
        from types import SimpleNamespace
        compatible = SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio) if name != 'Runner'})
        with patch('sinric_adapter.asyncio', compatible):
            adapter = SinricAdapter(Mock(), SETTINGS, transport_factory=FakeTransport)
            adapter.start()
            self.assertTrue(adapter.ready.wait(1))
            adapter.stop()
            self.assertFalse(adapter.thread.is_alive())

    def test_thread_shutdown_and_cancel_during_begin(self):
        for blocked in (False, True):
            instances = []
            cancelled = Event()
            created = Event()
            class Transport(FakeTransport):
                async def start(self):
                    self.started.set()
                    if blocked:
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cancelled.set()
                    else:
                        self.callbacks.connected()
            def factory(*args):
                transport = Transport(*args)
                instances.append(transport)
                created.set()
                return transport
            service = Mock()
            service.state.return_value = dict(power=True, brightness=80, scene='clock', revision=0)
            adapter = SinricAdapter(service, SETTINGS, transport_factory=factory)
            adapter.start()
            self.assertTrue(adapter.ready.wait(1))
            self.assertTrue(created.wait(1))
            self.assertTrue(instances[0].started.wait(1))
            adapter.stop()
            self.assertFalse(adapter.thread.is_alive())
            self.assertTrue(instances[0].closed.is_set())
            self.assertFalse(asyncio.run(adapter.callbacks.power(True)))
            if blocked:
                self.assertTrue(cancelled.is_set())

    def test_disabled_config_and_safe_startup_error(self):
        for settings, error, expected in [(None, '', 'disabled'), (None, 'secret payload', 'error')]:
            adapter = SinricAdapter(Mock(), settings, configuration_error=error)
            adapter.start()
            adapter.stop()
            self.assertEqual(adapter.status()['state'], expected)
            self.assertNotIn('secret', str(adapter.status()))
        failed = Event()
        class Broken(FakeTransport):
            async def start(self):
                failed.set()
                raise RuntimeError('secret signed payload')
        adapter = SinricAdapter(Mock(), SETTINGS, transport_factory=Broken)
        adapter.start()
        self.assertTrue(failed.wait(1))
        adapter.thread.join(2)
        adapter.stop()
        self.assertEqual(adapter.status()['state'], 'error')
        self.assertNotIn('secret', str(adapter.status()))
