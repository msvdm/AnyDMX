"""Tests for the sentences the window says.

This is the app's whole diagnostic vocabulary — every "why is nothing
happening" question the GUI exists to answer. No Qt is imported: the point of
pulling this out of the window was to be able to test it like anything else.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gui.status_text import artnet_status, bottom_line, dmx_status


def status(**overrides):
    st = {
        "artnet_active": False, "artnet_source": None, "artnet_pps": 0.0,
        "frame_len": 0, "holding": False, "held_since": 0.0, "poller_ip": None,
        "dmx_enabled": False, "dmx_connected": False, "dmx_fps": 0.0,
        "dmx_error": None,
    }
    st.update(overrides)
    return st


# ------------------------------------------------------------- art-net side

def test_receiving_names_the_source_and_the_rate():
    line = artnet_status(status(artnet_active=True, artnet_source="2.0.0.5",
                                artnet_pps=44.0, frame_len=512), 3, [])
    assert line.led == "ok"
    assert "44 pkt/s" in line.short
    assert "universe 3" in line.sentence and "2.0.0.5" in line.sentence
    assert "512 ch" in line.sentence


def test_a_local_test_sender_is_never_allowed_to_pass_for_a_console():
    line = artnet_status(status(artnet_active=True, artnet_source="127.0.0.1",
                                artnet_pps=30.0, frame_len=512), 0, [])
    assert "LOCAL TEST SENDER" in line.sentence


def test_a_held_frame_is_always_labelled_as_held():
    """An unlabelled frozen frame looks exactly like a live one."""
    line = artnet_status(status(holding=True, artnet_source="2.0.0.5",
                                held_since=12.0), 0, [])
    assert line.led == "warn"
    assert line.short == "holding last frame"
    assert "holding last frame" in line.sentence and "12s" in line.sentence


def test_holding_without_a_known_source_still_reads():
    line = artnet_status(status(holding=True, held_since=3.0), 0, [])
    assert "the last source" in line.sentence


def test_traffic_on_another_universe_says_which_one():
    line = artnet_status(status(), 0, [5, 7])
    assert line.short == "wrong universe"
    assert "universe 5, 7" in line.sentence
    assert "universe 0" in line.sentence


def test_a_console_that_found_us_but_sends_nothing_is_distinguished():
    line = artnet_status(status(poller_ip="2.0.0.9"), 0, [])
    assert line.short == "discovered, no DMX"
    assert "2.0.0.9" in line.sentence


def test_silence_is_reported_as_listening():
    line = artnet_status(status(), 0, [])
    assert line.short == "listening"
    assert "6454" in line.sentence


def test_live_traffic_outranks_every_other_art_net_state():
    """artnet_active wins even when the other flags would also match."""
    line = artnet_status(status(artnet_active=True, artnet_source="2.0.0.5",
                                holding=True, poller_ip="2.0.0.9"), 0, [5])
    assert line.led == "ok"


# ----------------------------------------------------------------- dmx side

def test_monitor_mode_is_not_an_error():
    line = dmx_status(status(dmx_enabled=False))
    assert line.led == "warn"
    assert line.short == "monitor mode"


def test_streaming_reports_the_rate():
    line = dmx_status(status(dmx_enabled=True, dmx_connected=True, dmx_fps=34.0))
    assert line.led == "ok"
    assert "34 fps" in line.short


def test_a_disconnected_dongle_surfaces_the_driver_error():
    line = dmx_status(status(dmx_enabled=True, dmx_error="Access is denied"))
    assert line.led == "err"
    assert "Access is denied" in line.sentence


def test_a_disconnected_dongle_without_an_error_still_says_something():
    line = dmx_status(status(dmx_enabled=True))
    assert "port unavailable" in line.sentence


# --------------------------------------------------------------- bottom line

def test_a_streaming_output_drops_out_of_the_bottom_line():
    """The boring case gives its half of the scarce space back to the input."""
    artnet = artnet_status(status(artnet_active=True, artnet_source="2.0.0.5"), 0, [])
    dmx = dmx_status(status(dmx_enabled=True, dmx_connected=True, dmx_fps=34.0))
    level, text = bottom_line(artnet, dmx)
    assert level == "ok"
    assert "DMX out" not in text


def test_an_output_error_is_carried_and_outranks_the_input_state():
    artnet = artnet_status(status(artnet_active=True, artnet_source="2.0.0.5"), 0, [])
    dmx = dmx_status(status(dmx_enabled=True, dmx_error="port gone"))
    level, text = bottom_line(artnet, dmx)
    assert level == "err"            # not the input's "ok"
    assert "port gone" in text and "Art-Net" in text


def test_monitor_mode_keeps_the_input_level():
    artnet = artnet_status(status(), 0, [])
    dmx = dmx_status(status(dmx_enabled=False))
    level, text = bottom_line(artnet, dmx)
    assert level == "warn"
    assert "monitor mode" in text
