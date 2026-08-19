"""JSON settings persistence (settings/settings.json)."""

import json

from src.utils.logger import get_logger
from src.utils.paths import settings_dir

log = get_logger(__name__)

DEFAULTS = {
    "com_port": "",
    "universe": 0,
    "fps": 40,
    "bind_ip": "",
    # Dedicated lighting interface (see src/core/vnet.py)
    "vnet_name": "AnyDMX",
    "vnet_ip": "2.100.100.0",
    "vnet_prefix": 8,
    "vnet_instance_id": "",
}


def _settings_file():
    return settings_dir() / "settings.json"


def load_settings():
    data = dict(DEFAULTS)
    try:
        with open(_settings_file(), "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored and isinstance(stored[key], type(DEFAULTS[key])):
                    data[key] = stored[key]
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not load settings (%s) — using defaults", e)
    return data


def save_settings(data):
    path = _settings_file()
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(path)
    except OSError as e:
        log.warning("Could not save settings: %s", e)
