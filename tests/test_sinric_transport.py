import asyncio
import builtins
import json
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

from configuration import SinricSettings
from sinric_transport import SinricCallbacks, SinricTransport

try:
    from sinricpro import SinricPro
    from sinricpro.core.types import SinricProRequest
except ImportError:
    SinricPro = None

SETTINGS = SinricSettings('12345678-1234-1234-1234-123456789abc', 's' * 32, 'a' * 24, 'scene')


@unittest.skipIf(SinricPro is None, 'optional: install requirements-sinric.txt')
class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.callbacks = SinricCallbacks(AsyncMock(return_value=True), AsyncMock(return_value=True),
            AsyncMock(return_value=(True, 20)), AsyncMock(return_value=True), Mock(), Mock())
        self.transport = SinricTransport(SETTINGS, self.callbacks)
        self.socket = Mock(send=AsyncMock())
        async def connect(ws):
            ws.ws = self.socket
        self.network = patch('sinricpro.core.websocket_client.WebSocketClient.connect', connect)
        self.network.start()
        self.addCleanup(self.network.stop)
        self.connected = patch('sinricpro.core.websocket_client.WebSocketClient.is_connected', return_value=True)
        self.connected.start()
        self.addCleanup(self.connected.stop)
        self.disconnect = patch('sinricpro.core.websocket_client.WebSocketClient.disconnect', new_callable=AsyncMock)
        self.disconnect.start()
        self.addCleanup(self.disconnect.stop)
        await self.transport.start()

    async def asyncTearDown(self):
        await self.transport.close()
        # SDK stop cancels processors; the adapter's runner gathers them in production.
        await asyncio.sleep(0)

    async def test_raw_validation_precedes_sdk_defaults(self):
        device = self.transport.device
        cases = [('setBrightness', {}, ''), ('adjustBrightness', {}, ''), ('setPowerState', {}, ''),
                 ('setPowerState', {'state': 'on'}, ''), ('setPowerState', {'state': True}, ''),
                 ('setMode', {'mode': 'clock'}, ''), ('setMode', {'mode': 'clock'}, 'wrong'),
                 ('setMode', {'mode': 'unknown'}, 'scene'), ('setColor', {}, '')]
        for value in (True, False, None, 1.5, '40'):
            cases += [('setBrightness', {'brightness': value}, ''), ('adjustBrightness', {'brightnessDelta': value}, '')]
        cases += [('setBrightness', {'brightness': 40}, 'unexpected')]
        for action, value, instance in cases:
            with self.subTest(action=action, value=value):
                self.assertFalse(await device.handle_request(SinricProRequest(action, instance, value)))
        self.callbacks.power.assert_not_awaited()
        self.callbacks.brightness.assert_not_awaited()
        self.callbacks.adjust_brightness.assert_not_awaited()
        self.callbacks.mode.assert_not_awaited()
        for state in ('On', 'Off'):
            self.assertTrue(await device.handle_request(SinricProRequest('setPowerState', request_value={'state': state})))
        request = SinricProRequest('adjustBrightness', request_value={'brightnessDelta': -20})
        self.assertTrue(await device.handle_request(request))
        self.assertEqual(request.response_value, {'brightness': 20})
        self.assertTrue(await device.handle_request(SinricProRequest('setMode', 'scene', {'mode': 'moon'})))

    async def test_signed_events_limiter_and_no_sdk_queue(self):
        self.assertTrue(await self.transport.report_power(False))
        self.assertTrue(await self.transport.report_brightness(40))
        self.assertTrue(await self.transport.report_mode('moon'))
        messages = [json.loads(c.args[0]) for c in self.socket.send.await_args_list]
        self.assertEqual([m['payload']['action'] for m in messages], ['setPowerState', 'setBrightness', 'setMode'])
        self.assertEqual(messages[-1]['payload']['instanceId'], 'scene')
        self.assertEqual(messages[-1]['payload']['value'], {'mode': 'moon'})
        self.assertTrue(all(self.transport.sdk.signature.validate(c.args[0]) for c in self.socket.send.await_args_list))
        self.assertFalse(await self.transport.report_brightness(50))
        self.assertEqual(len(self.transport.sdk.send_queue._queue), 0)
        with patch.object(self.transport.sdk, 'is_connected', return_value=False):
            self.assertFalse(await self.transport.report_mode('clock'))
        self.assertEqual(len(self.transport.sdk.send_queue._queue), 0)
        self.assertFalse(self.transport.sdk.config.local_control)
        self.assertFalse(self.transport.sdk.config.mdns)

    async def test_report_waits_for_send_and_propagates_failure(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(message):
            entered.set()
            await release.wait()
            raise OSError('dummy secret signed payload')
        self.socket.send.side_effect = send
        task = asyncio.create_task(self.transport.report_power(True))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertFalse(task.done())
        release.set()
        self.assertFalse(await task)

    async def test_close_partial_start_and_exclusive_owner(self):
        other = SinricTransport(SETTINGS, self.callbacks)
        with self.assertRaises(RuntimeError):
            await other.start()
        await other.close()
        self.assertTrue(self.transport.is_connected())
        sdk = self.transport.sdk
        with patch.object(sdk, 'stop', wraps=sdk.stop) as stop:
            await self.transport.close()
            stop.assert_awaited_once()


class OptionalImportTests(unittest.TestCase):
    def test_import_without_sdk(self):
        code = '''import builtins
real = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.startswith('sinricpro'): raise ImportError('blocked')
    return real(name, *args, **kwargs)
builtins.__import__ = blocked
import sinric_transport
'''
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)
