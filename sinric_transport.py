"""Version-coupled Sinric 6.0.0 boundary. Importing requires no SDK."""
from dataclasses import dataclass
from threading import Lock
from typing import Awaitable, Callable

from configuration import SinricSettings
from scene_catalogue import SCENE_IDS


@dataclass(frozen=True)
class SinricCallbacks:
    power: Callable[[bool], Awaitable[bool]]
    brightness: Callable[[int], Awaitable[bool]]
    adjust_brightness: Callable[[int], Awaitable[tuple[bool, int]]]
    mode: Callable[[str, str], Awaitable[bool]]
    connected: Callable[[], None]
    disconnected: Callable[[], None]


class _EventSender:
    """Await the current socket instead of retaining stale events in SDK queues."""
    def __init__(self, sdk, device_id):
        self.sdk, self.device_id = sdk, device_id

    def get_timestamp(self):
        return self.sdk.get_timestamp()

    async def send_message(self, message):
        payload = message.get('payload', {})
        if payload.get('type') != 'event' or payload.get('deviceId') != self.device_id:
            raise ValueError('Unsupported event.')
        sdk = self.sdk
        if not sdk.is_connected() or not sdk.signature or not sdk.websocket or not sdk.websocket.ws:
            raise ConnectionError('Sinric disconnected.')
        socket = sdk.websocket.ws
        await socket.send(sdk.signature.sign_message(message))
        if not sdk.is_connected() or sdk.websocket.ws is not socket:
            raise ConnectionError('Sinric connection changed.')


class SinricTransport:
    _owner_lock = Lock()

    def __init__(self, settings: SinricSettings, callbacks: SinricCallbacks):
        self.settings, self.callbacks = settings, callbacks
        self.sdk = self.device = None
        self._owns_sdk = False

    async def start(self) -> None:
        if self._owns_sdk:
            return
        try:
            from sinricpro import SinricPro, SinricProCustomDevice
            from sinricpro.core.types import SinricProConfig
            from sinricpro.utils.logger import SinricProLogger, LogLevel
        except ImportError:
            raise RuntimeError('Install requirements-sinric.txt and restart the controller.') from None
        SinricProLogger.set_level(LogLevel.NONE)
        if not self._owner_lock.acquire(blocking=False):
            raise RuntimeError('Only one Sinric controller may run in this process.')
        self._owns_sdk = True
        sdk = SinricPro.get_instance()
        if sdk.devices or sdk.is_initialized:
            self._owns_sdk = False
            self._owner_lock.release()
            raise RuntimeError('Sinric SDK already belongs to another controller.')
        self.sdk = sdk
        instance = self.settings.scene_instance_id

        class ValidatedDevice(SinricProCustomDevice):
            async def handle_request(device, request):
                value, action = request.request_value, request.action
                if not isinstance(value, dict):
                    return False
                if action == 'setMode':
                    if request.instance != instance or type(value.get('mode')) is not str or value['mode'] not in SCENE_IDS:
                        return False
                elif request.instance:
                    return False
                elif action == 'setPowerState':
                    if value.get('state') not in ('On', 'Off'):
                        return False
                elif action in ('setBrightness', 'adjustBrightness'):
                    field = 'brightness' if action == 'setBrightness' else 'brightnessDelta'
                    if type(value.get(field)) is not int:
                        return False
                else:
                    return False
                try:
                    return await super().handle_request(request)
                except Exception:
                    return False

        self.device = device = ValidatedDevice(self.settings.device_id, product_type='LIGHT')
        device.on_power_state(self.callbacks.power)
        device.on_brightness(self.callbacks.brightness)
        device.on_adjust_brightness(self.callbacks.adjust_brightness)
        device.on_mode_state(self.callbacks.mode)
        sdk.add(device)
        device.set_sinric_pro(_EventSender(sdk, self.settings.device_id))
        sdk.on_connected(self.callbacks.connected)
        sdk.on_disconnected(self.callbacks.disconnected)
        try:
            await sdk.begin(SinricProConfig(app_key=self.settings.app_key, app_secret=self.settings.app_secret,
                                           debug=False, local_control=False, mdns=False))
        except Exception:
            raise RuntimeError('Sinric startup failed. Check credentials and internet access, then restart.') from None

    def is_connected(self) -> bool:
        return bool(self.sdk and self.sdk.is_connected())

    async def report_power(self, value: bool) -> bool:
        return await self._report('send_power_state_event', value)

    async def report_brightness(self, value: int) -> bool:
        return await self._report('send_brightness_event', value)

    async def report_mode(self, value: str) -> bool:
        return await self._report('send_mode_event', value, instance_id=self.settings.scene_instance_id)

    async def _report(self, method, value, **kwargs) -> bool:
        if not self.is_connected() or self.device is None:
            return False
        try:
            return bool(await getattr(self.device, method)(value, **kwargs))
        except Exception:
            return False

    async def close(self) -> None:
        if not self._owns_sdk:
            return
        try:
            if self.sdk is not None:
                await self.sdk.stop()
        finally:
            # The pinned SDK singleton otherwise retains devices, callbacks and loop locks.
            from sinricpro import SinricPro
            if SinricPro._instance is self.sdk:
                SinricPro._instance = None
            self.sdk = self.device = None
            self._owns_sdk = False
            self._owner_lock.release()
