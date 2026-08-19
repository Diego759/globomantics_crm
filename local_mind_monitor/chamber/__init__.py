"""The Chamber: binaural / isochronic entrainment, closed around the headband.

Layers, each usable on its own:

    journeys.py  frequency arcs as data (built-in library + user JSON)
    synth.py     click-free stereo synthesis (numpy only)
    adaptive.py  the closed loop -- band power in, beat frequency out
    session.py   journey clock + synth + loop + CSV log
    render.py    offline export to a plain WAV (stdlib + numpy only)
    player.py    live output through sounddevice
    cli.py       list / show / render / play
"""

from .journeys import BUILTIN, BUILTIN_BY_KEY, Journey, Segment, band_for_beat, custom, get
from .session import ChamberSession, format_report

__all__ = [
    "BUILTIN",
    "BUILTIN_BY_KEY",
    "ChamberSession",
    "Journey",
    "Segment",
    "band_for_beat",
    "custom",
    "format_report",
    "get",
]
