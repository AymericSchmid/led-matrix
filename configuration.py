"""Non-executing project configuration shared by optional integrations."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re

ENV_FILE = Path(__file__).resolve().parent / '.env'


def dotenv_value(name: str, path: Path | None = None) -> str:
    try:
        lines = (path or ENV_FILE).read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeError):
        return ''
    for line in lines:
        key, separator, value = line.partition('=')
        if separator and key.strip() == name:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ''


def configured_value(name: str, path: Path | None = None) -> str:
    return os.environ.get(name, '').strip() or dotenv_value(name, path)


@dataclass(frozen=True)
class SinricSettings:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    device_id: str
    scene_instance_id: str


def load_sinric_settings(path: Path | None = None) -> SinricSettings | None:
    names = ('SINRIC_APP_KEY', 'SINRIC_APP_SECRET', 'SINRIC_DEVICE_ID', 'SINRIC_SCENE_INSTANCE_ID')
    values = [configured_value(name, path).strip() for name in names]
    if not any(values):
        return None
    missing = [name for name, value in zip(names, values) if not value]
    if missing:
        raise ValueError('Complete Sinric configuration in .env: ' + ', '.join(missing))
    key, secret, device, instance = values
    if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', key):
        raise ValueError('SINRIC_APP_KEY must be a UUID from the Sinric portal.')
    if len(secret) < 32:
        raise ValueError('SINRIC_APP_SECRET must contain at least 32 characters.')
    if not re.fullmatch(r'[0-9a-fA-F]{24}', device):
        raise ValueError('SINRIC_DEVICE_ID must be the 24 hexadecimal character device ID.')
    return SinricSettings(key, secret, device, instance)
