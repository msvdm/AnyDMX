"""JSON settings persistence: settings.json, beside the app."""

import json

from src.utils.paths import app_dir

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
    # Channel-grid drawer: False runs the compact window.
    "channels_expanded": True,
}


def _settings_file():
    return app_dir() / "settings.json"


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
    except (json.JSONDecodeError, OSError):
        pass  # unreadable or nowhere to read from: the defaults still work
    return data


def save_settings(data):
    """Persist the settings, or skip if there is nowhere to write them.

    A read-only location — a write-protected stick, Program Files — must
    cost the session its persistence and nothing else. This is called from GUI
    callbacks, where an OSError would take the window down.
    """
    try:
        path = _settings_file()
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(path)
    except OSError:
        pass
