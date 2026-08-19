"""Turning an engine status snapshot into what the window says.

This is the app's entire diagnostic vocabulary: the LED colour, the one-word
state on the panel, and the sentence along the bottom. It is kept out of the
window because it is a pure function of the snapshot — no widgets, no Qt — and
because these sentences are the thing most worth testing. Every "why is nothing
happening" question the GUI exists to answer is answered here.

Two rules run through all of it:

  * a held frame is always *labelled* as held. Levels nobody is sending look
    identical to live ones, and that reads as a bug every time
  * a local test sender is never allowed to pass for a console
"""

from typing import NamedTuple

from src.core.artnet_receiver import LOOPBACK


class StatusLine(NamedTuple):
    """What one side of the bridge is doing, at three levels of detail."""
    led: str        # "ok" | "warn" | "err" | "off"
    short: str      # the word on the panel, beside the LED
    sentence: str   # the full explanation for the bottom line


def artnet_status(st, universe, others):
    """The Art-Net side. `others` are universes seen but not selected."""
    if st["artnet_active"]:
        source = st["artnet_source"]
        origin = (f"{source} — LOCAL TEST SENDER, not your console"
                  if source == LOOPBACK else source)
        return StatusLine(
            "ok", f"receiving · {st['artnet_pps']:.0f} pkt/s",
            f"Art-Net: receiving universe {universe} from {origin} "
            f"({st['artnet_pps']:.0f} pkt/s, {st['frame_len']} ch)")
    if st["holding"]:
        # Not a fault — the last frame is held on purpose so the rig stays
        # lit. Saying so is the whole point: silence looks identical to it.
        source = st["artnet_source"] or "the last source"
        return StatusLine(
            "warn", "holding last frame",
            f"Art-Net: nothing arriving — holding last frame from "
            f"{source} ({st['held_since']:.0f}s ago)")
    if others:
        # The console is talking, just not on the universe we listen to.
        listed = ", ".join(str(u) for u in others)
        return StatusLine(
            "warn", "wrong universe",
            f"Art-Net: nothing on universe {universe} — but universe "
            f"{listed} is arriving. Click it to listen to it.")
    if st["poller_ip"]:
        return StatusLine(
            "warn", "discovered, no DMX",
            f"Art-Net: node visible to console at {st['poller_ip']} "
            "— waiting for DMX on this universe")
    return StatusLine("warn", "listening",
                      "Art-Net: listening on UDP 6454 — nothing is being sent")


def dmx_status(st):
    """The DMX side."""
    if not st["dmx_enabled"]:
        return StatusLine("warn", "monitor mode",
                          "DMX out: monitor mode — no output device selected")
    if st["dmx_connected"]:
        return StatusLine("ok", f"streaming · {st['dmx_fps']:.0f} fps",
                          f"DMX out: streaming ({st['dmx_fps']:.0f} fps)")
    err = st["dmx_error"] or "port unavailable"
    return StatusLine("err", "reconnecting", f"DMX out: reconnecting — {err}")


def bottom_line(artnet, dmx):
    """(level, sentence) for the status bar under both panels.

    The output's half is dropped while it is simply streaming — that is the
    boring case, and the bottom line is scarce space. An output error outranks
    whatever the input is doing, because it is the one that stops the show.
    """
    sentence = (artnet.sentence if dmx.led == "ok"
                else f"{artnet.sentence}    ·    {dmx.sentence}")
    return ("err" if dmx.led == "err" else artnet.led), sentence
