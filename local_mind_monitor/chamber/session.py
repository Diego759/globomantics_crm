"""Session runtime: journey clock, synth, closed loop and log in one object.

The audio callback pulls blocks out of :meth:`ChamberSession.render`, and
whoever has the headband pushes band powers in through :meth:`feed_bands`.
Those are the only two entry points, and they may be called from different
threads, so everything shared sits behind one small lock.

The clock is counted in *rendered audio frames* rather than wall time. That way
a live session and an offline render follow exactly the same timeline, and a
journey that says 20 minutes is 20 minutes of audio, not 20 minutes of whatever
the GUI timer managed to deliver.
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..bands import BANDS
from .adaptive import AdaptiveController, Baseline
from .journeys import Journey

PHASE_BASELINE = "baseline"
PHASE_PLAYING = "playing"
PHASE_DONE = "done"

# Ambience level held during the quiet baseline: enough to mask the room (so the
# baseline is measured in the same soundscape as the journey), no tones.
BASELINE_AMBIENCE = 0.25
# Seconds of silence rendered after the journey ends, so the fade-out completes
# before anything tears the stream down.
TAIL_SECONDS = 1.0


@dataclass(frozen=True)
class ChamberState:
    """Immutable snapshot for the UI -- safe to read from any thread."""

    phase: str
    elapsed: float
    journey_t: float
    remaining: float
    segment: str
    band: str
    carrier: float
    beat: float
    nominal_beat: float
    offset: float
    response_z: float | None
    baseline_progress: float
    adapting: bool
    holding: bool


class ChamberSession:
    def __init__(
        self,
        journey: Journey,
        sample_rate: int = 44100,
        volume: float = 0.85,
        mode: str | None = None,
        ambience_kind: str | None = None,
        adaptive: bool = False,
        baseline_seconds: float = 60.0,
        log_path: str | Path | None = None,
    ):
        from .synth import BinauralSynth  # local: keeps import cost off the GUI start-up

        self.journey = journey
        self.sample_rate = int(sample_rate)
        self.volume = float(np.clip(volume, 0.0, 1.0))
        self.adaptive = bool(adaptive)
        self.baseline_seconds = float(baseline_seconds) if adaptive else 0.0

        self.synth = BinauralSynth(
            sample_rate=self.sample_rate,
            mode=mode or journey.mode,
            ambience_kind=ambience_kind or journey.ambience_kind,
            carrier=journey.segments[0].carrier_start,
            beat=journey.segments[0].beat_start,
            gain=0.0,
            ambience=0.0,
        )

        self._lock = threading.Lock()
        self._frames = 0
        self._phase = PHASE_BASELINE if self.baseline_seconds > 0 else PHASE_PLAYING
        self._baseline_samples: list[np.ndarray] = []
        self._controllers: dict[str, AdaptiveController] = {}
        self._beat_override: float | None = None
        self._current_band = journey.segments[0].band

        self._log_path = Path(log_path) if log_path else None
        self._log_file = None
        self._log_writer = None
        if self._log_path is not None:
            self._open_log()

    # ------------------------------------------------------------------ clock
    @property
    def audio_time(self) -> float:
        return self._frames / self.sample_rate

    @property
    def journey_t(self) -> float:
        return max(0.0, self.audio_time - self.baseline_seconds)

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def finished(self) -> bool:
        return self.journey_t >= self.journey.seconds + TAIL_SECONDS

    def state(self) -> ChamberState:
        with self._lock:
            frame = self.journey.at(self.journey_t)
            ctrl = self._controllers.get(frame.band)
            beat = self._beat_override if self._beat_override is not None else frame.beat
            return ChamberState(
                phase=self._phase,
                elapsed=self.audio_time,
                journey_t=self.journey_t,
                remaining=max(0.0, self.journey.seconds - self.journey_t),
                segment=frame.segment.name,
                band=frame.band,
                carrier=frame.carrier,
                beat=beat,
                nominal_beat=frame.beat,
                offset=(beat - frame.beat),
                response_z=ctrl.response_z if ctrl else None,
                baseline_progress=self._baseline_progress(),
                adapting=self.adaptive and self._phase == PHASE_PLAYING and ctrl is not None,
                holding=bool(ctrl.holding) if ctrl else False,
            )

    def _baseline_progress(self) -> float:
        if not self.adaptive or self.baseline_seconds <= 0:
            return 1.0
        return float(min(1.0, self.audio_time / self.baseline_seconds))

    # ------------------------------------------------------------------ audio
    def render(self, frames: int) -> np.ndarray:
        """Produce the next block of stereo audio and advance the session."""
        if frames <= 0:
            return np.zeros((0, 2), dtype=np.float32)
        with self._lock:
            if self._phase == PHASE_BASELINE and self.audio_time >= self.baseline_seconds:
                self._begin_playing()

            if self._phase == PHASE_BASELINE:
                # Rest measurement: ambience only, no tones.
                self.synth.set(gain=0.0, ambience=BASELINE_AMBIENCE)
            else:
                t = self.journey_t
                frame = self.journey.at(t)
                self._current_band = frame.band
                beat = frame.beat
                if self.adaptive:
                    ctrl = self._controllers.get(frame.band)
                    if ctrl is not None:
                        ctrl.nominal_beat = frame.beat
                        beat = ctrl.beat_now()
                        self._beat_override = beat
                self.synth.set(
                    carrier=frame.carrier,
                    beat=beat,
                    gain=frame.envelope * self.volume,
                    ambience=frame.ambience * frame.envelope,
                )
                if t >= self.journey.seconds and self._phase != PHASE_DONE:
                    self._phase = PHASE_DONE
                    self.synth.set(gain=0.0, ambience=0.0)

            self._frames += frames
            return self.synth.render(frames)

    def _begin_playing(self) -> None:
        """Freeze the rest baseline and build one controller per target band."""
        self._phase = PHASE_PLAYING
        if not self.adaptive:
            return
        samples = self._baseline_samples
        if len(samples) < 4:
            # Not enough rest data (headband off, or bad contact throughout):
            # run the journey open-loop rather than adapt to nonsense.
            self.adaptive = False
            return
        for band in self.journey.bands or (self._current_band,):
            if not band:
                continue
            baseline = Baseline.from_samples(BANDS.index(band), samples)
            first = next(
                (s.beat_start for s in self.journey.segments if s.band == band),
                self.journey.segments[0].beat_start,
            )
            self._controllers[band] = AdaptiveController(band, first, baseline=baseline)

    # ------------------------------------------------------------------ EEG in
    def feed_bands(self, relative: np.ndarray, quality_ok: bool = True) -> None:
        """Push one relative band-power vector (5 values, sums to ~1) in."""
        if relative is None:
            return
        rel = np.asarray(relative, dtype=float)
        if rel.shape != (len(BANDS),) or not np.all(np.isfinite(rel)):
            return
        with self._lock:
            if self._phase == PHASE_BASELINE:
                if quality_ok:
                    self._baseline_samples.append(rel)
                self._log_row(rel, quality_ok, None)
                return
            ctrl = self._controllers.get(self._current_band) if self.adaptive else None
            if ctrl is not None:
                beat = ctrl.update(
                    self.journey_t, rel,
                    nominal_beat=self.journey.at(self.journey_t).beat,
                    quality_ok=quality_ok,
                )
                self._beat_override = beat
            self._log_row(rel, quality_ok, ctrl)

    # ------------------------------------------------------------------ log
    def _open_log(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self._log_path, "w", newline="", encoding="utf-8")
        self._log_writer = csv.writer(self._log_file)
        self._log_writer.writerow(
            ["Elapsed", "Phase", "Segment", "TargetBand", "Carrier_Hz", "Beat_Hz",
             "NominalBeat_Hz", "Offset_Hz", "Response_z", "SignalOK"]
            + [f"Rel_{b}" for b in BANDS]
        )

    def _log_row(self, rel: np.ndarray, quality_ok: bool, ctrl: AdaptiveController | None) -> None:
        if self._log_writer is None:
            return
        frame = self.journey.at(self.journey_t)
        beat = self._beat_override if self._beat_override is not None else frame.beat
        z = None
        if ctrl is not None and ctrl.baseline.complete:
            z = ctrl.baseline.z(rel)
        self._log_writer.writerow(
            [f"{self.audio_time:.2f}", self._phase, frame.segment.name, frame.band,
             f"{frame.carrier:.2f}", f"{beat:.3f}", f"{frame.beat:.3f}",
             f"{beat - frame.beat:.3f}",
             "" if z is None else f"{z:.3f}", int(bool(quality_ok))]
            + [f"{v:.5f}" for v in rel]
        )
        if self._log_file is not None:
            self._log_file.flush()

    # ------------------------------------------------------------------ finish
    def stop(self) -> dict:
        """Fade out, close the log, and return the session report."""
        with self._lock:
            self.synth.set(gain=0.0, ambience=0.0)
            self._phase = PHASE_DONE
            report = self._report()
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
                self._log_writer = None
        return report

    def _report(self) -> dict:
        return {
            "journey": self.journey.key,
            "title": self.journey.title,
            "played_seconds": self.journey_t,
            "planned_seconds": self.journey.seconds,
            "completed": self.journey_t >= self.journey.seconds - 1.0,
            "adaptive": self.adaptive,
            "log": str(self._log_path) if self._log_path else None,
            "bands": {band: c.report() for band, c in self._controllers.items()},
        }

    def report(self) -> dict:
        with self._lock:
            return self._report()


def format_report(report: dict) -> str:
    """Human-readable session summary -- the bit an open-loop app cannot print."""
    lines = [
        f"{report['title']} ({report['journey']})",
        f"  played {report['played_seconds'] / 60:.1f} of {report['planned_seconds'] / 60:.1f} min"
        + ("" if report["completed"] else "  (stopped early)"),
    ]
    if not report["adaptive"]:
        lines.append("  open loop — no headband data, so nothing was measured.")
        return "\n".join(lines)
    if not report["bands"]:
        lines.append("  closed loop armed, but no band ever became the target.")
    for band, r in report["bands"].items():
        lines.append(f"  {band}:")
        if not r["baseline_complete"]:
            lines.append("    no usable rest baseline — signal quality too poor.")
            continue
        best, score = r["best_beat"], r["best_score"]
        if best is None:
            lines.append("    not enough held time to score a frequency.")
        else:
            verdict = (
                "clear response" if score >= 2.0 else
                "some response" if score >= 1.0 else
                "no measurable response"
            )
            lines.append(f"    best beat {best:.2f} Hz — {score:+.2f} SD above rest ({verdict})")
        if r["curve"]:
            curve = "  ".join(f"{b:.2f}Hz {s:+.2f}" for b, s, n in r["curve"])
            lines.append(f"    response curve: {curve}")
    return "\n".join(lines)
