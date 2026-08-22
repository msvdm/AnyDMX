"""Tests for settings persistence, and for surviving having nowhere to write.

settings.json sits beside the app when it can, so a portable copy carries its
settings with it: run from a stick, from Downloads, from Program Files. All
three can be read-only, so every path in here has to end in defaults or a
quiet no-op — never an exception out of a GUI callback. An installed copy
cannot write beside itself at all, and falls back to the user's config
directory rather than forgetting everything.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import paths, settings
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


# ------------------------------------------- where the settings file lives

def test_settings_stay_beside_a_portable_copy(tmp_path, monkeypatch):
    """A copy on a USB stick must carry its settings on the stick.

    This is the case worth protecting, which is why "beside the executable"
    is the first choice and the per-user directory is only the fallback.
    """
    monkeypatch.setattr(paths, "_executable_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "user_config_dir", lambda: tmp_path / "elsewhere")
    assert paths.app_dir() == tmp_path


def test_an_installed_copy_falls_back_to_the_user_config_dir(tmp_path,
                                                             monkeypatch):
    """/usr/lib is not the user's to write to — that is what installing means.

    Without this an installed AnyDMX silently forgets every setting, because
    save_settings() treats an unwritable location as losing persistence and
    nothing else. It would look like a bug with no error attached to it.
    """
    readonly = tmp_path / "usr_lib_anydmx"
    readonly.mkdir()
    config = tmp_path / "config"
    monkeypatch.setattr(paths, "_executable_dir", lambda: readonly)
    monkeypatch.setattr(paths, "user_config_dir", lambda: config)
    monkeypatch.setattr(paths.os, "access", lambda p, mode: False)
    assert paths.app_dir() == config


def test_the_config_directory_is_created_on_first_save(tmp_path, monkeypatch):
    """A first run of an installed copy has no ~/.config/AnyDMX yet."""
    fresh = tmp_path / "not" / "there" / "yet"
    monkeypatch.setattr(settings, "app_dir", lambda: fresh)
    data = dict(settings.DEFAULTS)
    data["universe"] = 7
    settings.save_settings(data)
    assert (fresh / "settings.json").exists()
    assert settings.load_settings()["universe"] == 7


def test_the_user_config_dir_follows_the_platform(monkeypatch):
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg")
    assert paths.user_config_dir() == Path("/tmp/xdg/AnyDMX")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert paths.user_config_dir() == Path.home() / ".config" / "AnyDMX"
