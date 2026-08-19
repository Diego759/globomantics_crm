"""Journeys: declarative frequency arcs, and the built-in library.

A journey is just data -- a list of segments, each one gliding the beat and
carrier from A to B over a duration -- so the same definition can be played
live, rendered to a WAV, drawn as a timeline, or written by a user in JSON
without touching any code.

Every built-in journey below is plain text in this file. Nothing is locked, and
nothing is bought: if you want a 42-minute descent to 3.7 Hz, add it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..bands import BAND_RANGES, BANDS

# Seconds of fade at the start and end of a journey. Long, because the point is
# to not be noticed arriving or leaving.
FADE_IN = 8.0
FADE_OUT = 12.0


def band_for_beat(hz: float) -> str:
    """Name the EEG band a beat frequency is aiming at ("" if outside all bands).

    This is what ties the audio side to the measurement side: the band a
    segment targets is not a label someone typed, it is derived from the
    frequency actually being played, using the same band edges the analyser
    uses to score the EEG.
    """
    for name, (lo, hi) in zip(BANDS, BAND_RANGES):
        if lo <= hz < hi:
            return name
    return ""


@dataclass(frozen=True)
class Segment:
    """One leg of a journey. Frequencies glide linearly from start to end."""

    name: str
    seconds: float
    beat_start: float
    beat_end: float | None = None      # None -> hold at beat_start
    carrier_start: float = 200.0
    carrier_end: float | None = None   # None -> hold at carrier_start
    ambience: float = 0.35
    note: str = ""

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError(f"segment {self.name!r}: seconds must be > 0")
        if self.beat_start <= 0 or (self.beat_end is not None and self.beat_end <= 0):
            raise ValueError(f"segment {self.name!r}: beat frequency must be > 0")

    @property
    def end_beat(self) -> float:
        return self.beat_start if self.beat_end is None else self.beat_end

    @property
    def end_carrier(self) -> float:
        return self.carrier_start if self.carrier_end is None else self.carrier_end

    @property
    def band(self) -> str:
        """Target band, taken from the midpoint of the glide."""
        return band_for_beat((self.beat_start + self.end_beat) / 2.0)


@dataclass(frozen=True)
class Frame:
    """What the synth should be doing at one instant of a journey."""

    t: float
    segment_index: int
    segment: Segment
    carrier: float
    beat: float
    ambience: float
    envelope: float  # 0..1 journey-level fade
    band: str

    @property
    def gain(self) -> float:
        return self.envelope


@dataclass(frozen=True)
class Journey:
    key: str
    title: str
    summary: str
    segments: tuple[Segment, ...]
    mode: str = "binaural"
    ambience_kind: str = "pink"
    caution: str = ""

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.segments)

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0

    @property
    def bands(self) -> tuple[str, ...]:
        """Distinct target bands in order of appearance."""
        seen: list[str] = []
        for s in self.segments:
            if s.band and s.band not in seen:
                seen.append(s.band)
        return tuple(seen)

    def locate(self, t: float) -> tuple[int, Segment, float]:
        """Return (index, segment, seconds into that segment) at journey time t."""
        t = max(0.0, min(float(t), self.seconds))
        elapsed = 0.0
        for i, seg in enumerate(self.segments):
            if t < elapsed + seg.seconds or i == len(self.segments) - 1:
                return i, seg, min(t - elapsed, seg.seconds)
            elapsed += seg.seconds
        raise AssertionError("unreachable: journey has no segments")

    def at(self, t: float) -> Frame:
        """Sample the journey at time ``t`` seconds."""
        i, seg, local = self.locate(t)
        frac = local / seg.seconds if seg.seconds > 0 else 1.0
        beat = seg.beat_start + (seg.end_beat - seg.beat_start) * frac
        carrier = seg.carrier_start + (seg.end_carrier - seg.carrier_start) * frac
        return Frame(
            t=t,
            segment_index=i,
            segment=seg,
            carrier=carrier,
            beat=beat,
            ambience=seg.ambience,
            envelope=self.envelope_at(t),
            band=band_for_beat(beat),
        )

    def envelope_at(self, t: float) -> float:
        """Journey-level fade in/out, so nothing starts or stops abruptly."""
        total = self.seconds
        if t <= 0.0 or t >= total:
            return 0.0
        fade_in = min(FADE_IN, total / 2.0)
        fade_out = min(FADE_OUT, total / 2.0)
        rise = min(1.0, t / fade_in) if fade_in > 0 else 1.0
        fall = min(1.0, (total - t) / fade_out) if fade_out > 0 else 1.0
        return float(min(rise, fall))

    # ------------------------------------------------------------------ json
    def to_dict(self) -> dict:
        d = asdict(self)
        d["segments"] = [asdict(s) for s in self.segments]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Journey":
        known = ("key", "title", "summary", "mode", "ambience_kind", "caution")
        fields = {k: v for k, v in data.items() if k in known}
        return cls(segments=tuple(Segment(**s) for s in data["segments"]), **fields)

    @classmethod
    def load(cls, path: str | Path) -> "Journey":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def _min(m: float) -> float:
    return m * 60.0


# Carrier choices follow Oster's perception work: binaural beats are clearest
# with carriers in the low hundreds of Hz, and the lower the beat you are
# chasing the lower the carrier wants to be (~160-210 Hz for theta). The two
# journeys that keep a fixed musical carrier (528 Hz, 432 Hz) do so because
# people ask for them by name, not because the beat is clearer there.
BUILTIN: tuple[Journey, ...] = (
    Journey(
        key="first-descent",
        title="First Descent",
        summary="Alpha settle into theta. The one to start with.",
        segments=(
            Segment("Settle", _min(4), 10.0, carrier_start=210.0, ambience=0.45,
                    note="Eyes closed. Let the tone do the work."),
            Segment("Descend", _min(5), 10.0, 6.0, 210.0, 180.0, ambience=0.4),
            Segment("Hold", _min(5), 6.0, carrier_start=180.0, ambience=0.35,
                    note="Theta. Stay just this side of sleep."),
            Segment("Return", _min(2), 6.0, 10.0, 180.0, 210.0, ambience=0.45),
        ),
    ),
    Journey(
        key="astral-primer",
        title="Astral Energy Primer",
        summary="Alpha and beta into gamma sparks. A quick charge before deeper practice.",
        segments=(
            Segment("Charge", _min(3), 10.0, 14.0, 220.0, 240.0, ambience=0.25),
            Segment("Lift", _min(3), 14.0, 24.0, 240.0, 280.0, ambience=0.2),
            Segment("Sparks", _min(3), 40.0, carrier_start=300.0, ambience=0.15,
                    note="40 Hz. Bright and slightly electric."),
            Segment("Level", _min(1), 40.0, 12.0, 300.0, 220.0, ambience=0.3),
        ),
    ),
    Journey(
        key="golden-528",
        title="Golden Frequency",
        summary="528 Hz carrier with a calm alpha beat. A bright reset for warmth and return.",
        segments=(
            Segment("Open", _min(3), 9.0, carrier_start=528.0, ambience=0.3),
            Segment("Warm", _min(6), 9.0, 8.0, 528.0, 528.0, ambience=0.35),
            Segment("Close", _min(3), 8.0, 10.0, 528.0, 528.0, ambience=0.3),
        ),
    ),
    Journey(
        key="theta-gateway",
        title="Theta Gateway",
        summary="A long hold at 6.3 Hz — the frontal-midline theta band of deep meditation.",
        segments=(
            Segment("Approach", _min(5), 9.0, 6.3, 200.0, 185.0, ambience=0.4),
            Segment("Gateway", _min(20), 6.3, carrier_start=185.0, ambience=0.3),
            Segment("Return", _min(5), 6.3, 10.0, 185.0, 210.0, ambience=0.45),
        ),
    ),
    Journey(
        key="hypnagogic-hold",
        title="Hypnagogic Hold",
        summary="Sits at 4.5 Hz with periodic alpha nudges — the mind-awake/body-asleep edge.",
        segments=(
            Segment("Down", _min(6), 10.0, 4.5, 210.0, 170.0, ambience=0.45),
            Segment("Edge", _min(6), 4.5, carrier_start=170.0, ambience=0.3),
            Segment("Nudge", _min(1), 4.5, 9.5, 170.0, 200.0, ambience=0.3,
                    note="A brief lift to keep awareness from dissolving."),
            Segment("Edge again", _min(8), 9.5, 4.5, 200.0, 170.0, ambience=0.3),
            Segment("Nudge", _min(1), 4.5, 9.5, 170.0, 200.0, ambience=0.3),
            Segment("Rest", _min(8), 9.5, 4.5, 200.0, 170.0, ambience=0.35),
        ),
        caution="Do not run this while driving or operating anything.",
    ),
    Journey(
        key="deep-delta",
        title="Deep Delta",
        summary="A 25-minute descent to 2.5 Hz and out. Built for falling asleep to.",
        segments=(
            Segment("Unwind", _min(5), 9.0, 6.0, 200.0, 175.0, ambience=0.5),
            Segment("Sink", _min(7), 6.0, 3.5, 175.0, 150.0, ambience=0.55),
            Segment("Delta", _min(13), 2.5, carrier_start=140.0, ambience=0.6,
                    note="No return leg — this one is meant to end with you asleep."),
        ),
        ambience_kind="brown",
        caution="Do not run this while driving or operating anything.",
    ),
    Journey(
        key="power-nap",
        title="Power Nap",
        summary="20 minutes down to delta and deliberately back up to beta, so you wake sharp.",
        segments=(
            Segment("Drop", _min(4), 10.0, 5.0, 205.0, 170.0, ambience=0.5),
            Segment("Nap", _min(11), 3.0, carrier_start=150.0, ambience=0.55),
            Segment("Surface", _min(3), 3.0, 10.0, 150.0, 210.0, ambience=0.4),
            Segment("Wake", _min(2), 10.0, 16.0, 210.0, 240.0, ambience=0.2,
                    note="The bit every nap timer forgets: an actual ramp back up."),
        ),
        ambience_kind="brown",
    ),
    Journey(
        key="schumann",
        title="Schumann Resonance",
        summary="A steady 7.83 Hz — the Earth–ionosphere cavity resonance, low alpha.",
        segments=(
            Segment("Tune", _min(3), 10.0, 7.83, 200.0, 190.0, ambience=0.4),
            Segment("Resonate", _min(14), 7.83, carrier_start=190.0, ambience=0.35),
            Segment("Release", _min(3), 7.83, 10.0, 190.0, 200.0, ambience=0.4),
        ),
    ),
    Journey(
        key="focus-beta",
        title="Work Focus",
        summary="Low beta for sustained work. Long, flat, and deliberately uneventful.",
        segments=(
            Segment("Spin up", _min(3), 10.0, 15.0, 220.0, 250.0, ambience=0.2),
            Segment("Focus", _min(38), 15.0, 17.0, 250.0, 250.0, ambience=0.15,
                    note="Drifts a couple of Hz across the block so it never becomes wallpaper."),
            Segment("Wind down", _min(4), 17.0, 11.0, 250.0, 220.0, ambience=0.3),
        ),
    ),
    Journey(
        key="gamma-spark",
        title="Gamma Spark",
        summary="Ten minutes at 40 Hz. Short by design — gamma is a sprint, not a bed.",
        segments=(
            Segment("Ramp", _min(2), 14.0, 40.0, 240.0, 300.0, ambience=0.2),
            Segment("40 Hz", _min(6), 40.0, carrier_start=300.0, ambience=0.15),
            Segment("Down", _min(2), 40.0, 12.0, 300.0, 220.0, ambience=0.3),
        ),
    ),
    Journey(
        key="calm-reset",
        title="Calm Reset",
        summary="Ten quiet minutes at 10 Hz with a brown-noise bed. The everyday one.",
        segments=(
            Segment("In", _min(2), 10.0, carrier_start=200.0, ambience=0.5),
            Segment("Hold", _min(6), 10.0, 9.0, 200.0, 195.0, ambience=0.5),
            Segment("Out", _min(2), 9.0, 10.0, 195.0, 200.0, ambience=0.5),
        ),
        ambience_kind="brown",
    ),
    Journey(
        key="open-room",
        title="Open Room (speakers)",
        summary="Isochronic pulses instead of binaural — works without headphones.",
        segments=(
            Segment("Settle", _min(4), 10.0, carrier_start=180.0, ambience=0.4),
            Segment("Descend", _min(6), 10.0, 7.0, 180.0, 170.0, ambience=0.35),
            Segment("Hold", _min(8), 7.0, carrier_start=170.0, ambience=0.35),
            Segment("Return", _min(2), 7.0, 10.0, 170.0, 180.0, ambience=0.4),
        ),
        mode="isochronic",
    ),
)

BUILTIN_BY_KEY = {j.key: j for j in BUILTIN}


def get(key: str) -> Journey:
    """Look up a built-in journey, or load one from a .json path."""
    if key in BUILTIN_BY_KEY:
        return BUILTIN_BY_KEY[key]
    path = Path(key)
    if path.suffix == ".json" and path.exists():
        return Journey.load(path)
    raise KeyError(
        f"unknown journey {key!r}. Built-ins: {', '.join(sorted(BUILTIN_BY_KEY))}"
    )


def user_journeys(directory: str | Path) -> list[Journey]:
    """Load every .json journey in a directory (missing directory -> nothing)."""
    d = Path(directory)
    if not d.is_dir():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            out.append(Journey.load(path))
        except Exception:
            continue  # a malformed file should never stop the app from starting
    return out


def custom(beat: float, minutes: float, carrier: float = 200.0,
           mode: str = "binaural", ambience: float = 0.35) -> Journey:
    """Build a one-segment journey: hold a frequency for a while."""
    band = band_for_beat(beat)
    return Journey(
        key="custom",
        title=f"{beat:g} Hz hold",
        summary=f"{minutes:g} min at {beat:g} Hz ({band or 'out of band'}), {carrier:g} Hz carrier.",
        segments=(Segment("Hold", _min(minutes), beat, carrier_start=carrier, ambience=ambience),),
        mode=mode,
    )
