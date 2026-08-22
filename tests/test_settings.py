"""Tests for settings persistence, and for surviving having nowhere to write.

settings.json sits beside the app, so wherever the app is, the settings are.
The app is meant to be portable: run from a stick, from Downloads, from
Program Files. All three can be read-only, so every path in here has to end in
defaults or a quiet no-op — never an exception out of a GUI callback.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import settings
from src.utils.settings import DEFAULTS, load_settings, save_settings


def point_at(monkeypatch, directory):
    monkeypatch.setattr(settings, "app_dir", lambda: Path(directory))


def point_nowhere(monkeypatch, tmp_path):
    """A location that cannot be written: no chmod, so Windows counts too."""
    point_at(monkeypatch, tmp_path / "does" / "not" / "exist")


# ------------------------------------------------------------- round trip

def test_what_was_saved_is_what_comes_back(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path)
    save_settings(dict(DEFAULTS, universe=5, com_port="COM7"))
    loaded = load_settings()
    assert loaded["universe"] == 5
    assert loaded["com_port"] == "COM7"


def test_a_value_of_the_wrong_type_is_ignored_not_trusted(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"universe": "five"}))
    assert load_settings()["universe"] == DEFAULTS["universe"]


def test_no_temp_file_is_left_behind(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path)
    save_settings(dict(DEFAULTS))
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


# --------------------------------------------------- nowhere to read/write

def test_a_missing_file_is_not_an_error(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path)
    assert load_settings() == DEFAULTS


def test_a_corrupt_file_falls_back_to_the_defaults(tmp_path, monkeypatch):
    point_at(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text("{not json at all")
    assert load_settings() == DEFAULTS


def test_saving_with_nowhere_to_write_is_skipped_not_raised(tmp_path,
                                                            monkeypatch):
    """A read-only app dir costs the session its persistence, nothing more.

    save_settings runs from GUI callbacks — every combo change calls it — so
    an OSError escaping here takes the window down with it.
    """
    point_nowhere(monkeypatch, tmp_path)
    save_settings(dict(DEFAULTS, universe=3))  # must not raise


def test_loading_with_nowhere_to_read_gives_the_defaults(tmp_path, monkeypatch):
    point_nowhere(monkeypatch, tmp_path)
    assert load_settings() == DEFAULTS
