"""Offline rendering: turn a journey into a plain WAV file.

This is the answer to "is there an Android version?". There isn't, and there
doesn't need to be: render the journey once here and the result is an ordinary
stereo WAV that plays on any phone, any player, offline, forever -- including
one re-tuned to the frequency your own EEG responded to best.

Only the standard library and numpy are involved, so export works on a machine
with no audio hardware, no BrainFlow and no Qt.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable, Mapping

import numpy as np

from .journeys import Journey
from .synth import BinauralSynth

BLOCK = 8192  # frames per write; streaming keeps a 45-minute render in ~1 MB


def render_journey(
    journey: Journey,
    path: str | Path,
    sample_rate: int = 44100,
    volume: float = 0.85,
    mode: str | None = None,
    ambience_kind: str | None = None,
    beat_offsets: Mapping[str, float] | None = None,
    progress: Callable[[float], None] | None = None,
) -> Path:
    """Write ``journey`` to a 16-bit stereo WAV at ``path``; return the path.

    ``beat_offsets`` maps a band name to the Hz offset learnt for it in an
    adaptive session (``{"Alpha": -0.8}``), so an exported file can carry your
    personal tuning instead of the nominal frequency.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    synth = BinauralSynth(
        sample_rate=sample_rate,
        mode=mode or journey.mode,
        ambience_kind=ambience_kind or journey.ambience_kind,
        carrier=journey.segments[0].carrier_start,
        beat=journey.segments[0].beat_start,
        gain=0.0,
        ambience=0.0,
    )
    total_frames = int(journey.seconds * sample_rate)
    rng = np.random.default_rng(0)

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        written = 0
        while written < total_frames:
            n = min(BLOCK, total_frames - written)
            t = written / sample_rate
            frame = journey.at(t)
            beat = frame.beat + _offset_for(beat_offsets, frame.band)
            synth.set(
                carrier=frame.carrier,
                beat=beat,
                gain=frame.envelope * volume,
                ambience=frame.ambience * frame.envelope,
            )
            if written == 0:
                synth.snap()  # start already at the right settings, not sliding up to them
            wav.writeframes(_to_pcm16(synth.render(n), rng))
            written += n
            if progress is not None:
                progress(written / total_frames)
    return path


def _offset_for(offsets: Mapping[str, float] | None, band: str) -> float:
    if not offsets or not band:
        return 0.0
    return float(offsets.get(band, 0.0))


def _to_pcm16(block: np.ndarray, rng: np.random.Generator) -> bytes:
    """Quantise float audio to 16-bit with TPDF dither.

    Pure sine tones fading to silence are the worst case for plain truncation --
    the quantisation error correlates with the signal and you hear it as grit on
    the fade. A triangular dither of one LSB decorrelates it for the cost of
    inaudible noise.
    """
    dither = (rng.random(block.shape) - rng.random(block.shape)) / 32768.0
    x = np.clip(block.astype(np.float64) + dither, -1.0, 1.0)
    return (x * 32767.0).astype("<i2").tobytes()


def estimate_bytes(journey: Journey, sample_rate: int = 44100) -> int:
    """Size of the WAV this journey would produce (16-bit stereo)."""
    return int(journey.seconds * sample_rate) * 4 + 44
