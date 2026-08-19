"""The closed loop: use the EEG to decide what frequency to play.

Every binaural app on the store is open loop. It plays 10 Hz at you and assumes
something happened. The evidence that a fixed beat entrains cortical rhythms is
genuinely mixed, and the studies that do find an effect find it is *personal* --
which is an argument for measuring rather than for believing.

With a headband on, the loop closes:

  baseline  ->  play a candidate beat  ->  score the target band against
  baseline  ->  step the beat toward whatever actually moved this brain

Nothing here talks to BrainFlow or Qt. It takes band powers in and gives a beat
frequency out, so the whole controller can be exercised against synthetic input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..bands import BANDS

# Defaults chosen from how the measurement actually behaves: band powers come
# off a 2 s PSD window, entrainment (where it exists) builds over tens of
# seconds, and EEG band power drifts on its own, so a candidate has to be held
# long enough to out-average the drift.
BASELINE_SECONDS = 60.0
SETTLE_SECONDS = 15.0     # ignored at the start of each candidate
DWELL_SECONDS = 45.0      # total time per candidate, settle included
SEARCH_SPAN = 1.5         # Hz either side of the journey's nominal beat
SEARCH_STEP = 0.4         # Hz per hill-climb move
SMOOTHING = 0.05          # EMA weight for the displayed response index
MIN_SCORED_SAMPLES = 8    # below this a candidate is re-run, not scored


@dataclass
class Baseline:
    """Resting statistics for one band, gathered before the tones come up."""

    band_index: int
    seconds: float = BASELINE_SECONDS
    values: list[float] = field(default_factory=list)
    mean: float = 0.0
    std: float = 0.0
    complete: bool = False
    _start: float | None = None

    # The baseline is only meaningful if it is measured at *rest* -- tones down.
    # Collect it while the beat is already playing and the response gets folded
    # into the reference, which reads back as "nothing happened". ChamberSession
    # handles this by keeping the synth silent for its whole baseline phase.
    def add(self, t: float, relative: np.ndarray) -> None:
        if self.complete:
            return
        if self._start is None:
            self._start = t
        self.values.append(float(relative[self.band_index]))
        if t - self._start >= self.seconds and len(self.values) >= 4:
            self.finish()

    def finish(self) -> None:
        """Freeze the baseline from whatever has been collected so far."""
        if self.complete or not self.values:
            return
        arr = np.asarray(self.values, dtype=float)
        self.mean = float(arr.mean())
        # A floor on the spread keeps a freakishly steady baseline from turning
        # ordinary noise into a z-score of 40.
        self.std = float(max(arr.std(), 1e-3))
        self.complete = True

    @classmethod
    def from_samples(cls, band_index: int, samples: list[np.ndarray]) -> "Baseline":
        """Build a finished baseline from already-collected band-power vectors.

        Used for the quiet baseline the session records before the tones come
        up: one rest measurement feeds a controller for every band, so switching
        target band mid-journey does not cost another minute of silence.
        """
        b = cls(band_index)
        b.values = [float(v[band_index]) for v in samples]
        b.finish()
        return b

    def progress(self, t: float) -> float:
        if self.complete:
            return 1.0
        if self._start is None:
            return 0.0
        return float(min(1.0, (t - self._start) / self.seconds))

    def z(self, relative: np.ndarray) -> float | None:
        """How far the target band is above rest, in baseline standard deviations."""
        if not self.complete:
            return None
        return float((relative[self.band_index] - self.mean) / self.std)


@dataclass
class Candidate:
    """One tested beat frequency and what the brain did during it."""

    beat: float
    samples: list[float] = field(default_factory=list)

    @property
    def score(self) -> float:
        return float(np.mean(self.samples)) if self.samples else float("nan")

    @property
    def n(self) -> int:
        return len(self.samples)


class AdaptiveController:
    """Hill-climbs the beat frequency toward the strongest measured response.

    Deliberately a coordinate search rather than anything gradient-based: the
    signal is noisy, samples arrive a couple of times a second, and a session
    only affords a handful of candidates. Hold, average, step toward better,
    reverse at the edges -- and never move faster than the ear can be led.
    """

    def __init__(
        self,
        band: str,
        nominal_beat: float,
        span: float = SEARCH_SPAN,
        step: float = SEARCH_STEP,
        dwell: float = DWELL_SECONDS,
        settle: float = SETTLE_SECONDS,
        baseline_seconds: float = BASELINE_SECONDS,
        baseline: Baseline | None = None,
    ):
        if band not in BANDS:
            raise ValueError(f"unknown band {band!r}; expected one of {BANDS}")
        self.band = band
        self.band_index = BANDS.index(band)
        self.nominal_beat = float(nominal_beat)
        self.span = float(span)
        self.step = float(step)
        self.dwell = float(dwell)
        self.settle = float(settle)

        if baseline is not None and baseline.band_index != self.band_index:
            raise ValueError("baseline is for a different band")
        self.baseline = baseline or Baseline(self.band_index, baseline_seconds)
        self.offset = 0.0            # current Hz offset from the journey's beat
        self._direction = 1.0
        self._scores: dict[float, float] = {}   # offset -> mean z, for the search
        self.converged = False
        self._candidate = Candidate(self.nominal_beat)
        self._candidate_start: float | None = None
        self.history: list[Candidate] = []
        self.response_z: float | None = None      # smoothed, for display
        self._last_z: float | None = None
        self.holding = False         # true while paused on bad signal quality
        self.samples_seen = 0

    # ------------------------------------------------------------------ state
    @property
    def searching(self) -> bool:
        return self.baseline.complete

    @property
    def best(self) -> Candidate | None:
        """The best-scoring candidate with enough samples to be believed."""
        scored = [c for c in self.history if c.n >= MIN_SCORED_SAMPLES]
        return max(scored, key=lambda c: c.score) if scored else None

    def curve(self) -> list[tuple[float, float, int]]:
        """(beat, score, samples) for every candidate tried, ascending by beat.

        This is the artefact the whole design exists to produce: your personal
        response curve, which no fixed-frequency track can give you.
        """
        merged: dict[float, Candidate] = {}
        for c in self.history:
            key = round(c.beat, 2)
            if key in merged:
                merged[key].samples.extend(c.samples)
            else:
                merged[key] = Candidate(key, list(c.samples))
        return sorted(
            ((c.beat, c.score, c.n) for c in merged.values() if c.n > 0),
            key=lambda row: row[0],
        )

    # ------------------------------------------------------------------ update
    def update(
        self,
        t: float,
        relative: np.ndarray,
        nominal_beat: float | None = None,
        quality_ok: bool = True,
    ) -> float:
        """Feed one band-power sample; return the beat frequency to play now.

        ``relative`` is the 5-element relative band-power vector (BrainFlow's
        normalised powers). ``t`` is seconds since the session started.
        """
        if nominal_beat is not None:
            self.nominal_beat = float(nominal_beat)

        # A loose electrode produces exactly the kind of large, confident-looking
        # band-power change that would poison the search, so drop those samples
        # and freeze rather than learning from them.
        self.holding = not quality_ok
        if not quality_ok or relative is None or not np.all(np.isfinite(relative)):
            return self.beat_now()

        self.samples_seen += 1
        if not self.baseline.complete:
            self.baseline.add(t, relative)
            return self.beat_now()

        z = self.baseline.z(relative)
        if z is None:
            return self.beat_now()
        self._last_z = z
        self.response_z = z if self.response_z is None else (
            (1.0 - SMOOTHING) * self.response_z + SMOOTHING * z
        )

        if self._candidate_start is None:
            self._candidate_start = t
            self._candidate = Candidate(self.beat_now())
        elapsed = t - self._candidate_start
        if elapsed >= self.settle:
            self._candidate.samples.append(z)
        if elapsed >= self.dwell:
            self._finish_candidate(t)
        return self.beat_now()

    def _finish_candidate(self, t: float) -> None:
        """Score the candidate just held and choose the next offset to try."""
        if self._candidate.n < MIN_SCORED_SAMPLES:
            # Too little usable data (bad contact for most of the dwell): give
            # this candidate another go instead of scoring noise.
            self._candidate_start = t
            return

        self.history.append(self._candidate)
        key = round(self.offset, 2)
        # Re-visiting an offset averages the two visits rather than trusting the
        # newer one -- band power drifts over a session, and a candidate tested
        # late should not beat an identical one tested early just for that.
        self._scores[key] = (
            self._candidate.score if key not in self._scores
            else 0.5 * (self._scores[key] + self._candidate.score)
        )
        self.offset = self._next_offset()
        self._candidate_start = t
        self._candidate = Candidate(self.beat_now())

    def _next_offset(self) -> float:
        """Step toward the best offset found so far, then past it, then settle.

        Climbing from the *best* rather than from the last thing tried is what
        stops the search from wandering off a good frequency it already found --
        and once both neighbours of the best have been tried and lost, there is
        nothing left to climb, so it stays on the winner.
        """
        best_offset = max(self._scores, key=self._scores.__getitem__)
        limit = self.span
        neighbours = [
            round(best_offset + self._direction * self.step, 2),
            round(best_offset - self._direction * self.step, 2),
        ]
        for i, cand in enumerate(neighbours):
            if abs(cand) > limit + 1e-9 or cand in self._scores:
                continue
            if i == 1:
                self._direction *= -1.0  # took the other side; keep going that way
            return float(cand)
        # Both neighbours already tried and neither beat it: sit on the best.
        self.converged = True
        return float(best_offset)

    def beat_now(self) -> float:
        """The frequency to play right now: journey's beat plus learnt offset."""
        if not self.baseline.complete:
            return self.nominal_beat
        return float(max(0.5, self.nominal_beat + self.offset))

    # ------------------------------------------------------------------ report
    def report(self) -> dict:
        """End-of-session summary: did anything actually move, and where."""
        best = self.best
        curve = self.curve()
        scored = [row for row in curve if row[2] >= MIN_SCORED_SAMPLES]
        return {
            "band": self.band,
            "baseline_mean": self.baseline.mean,
            "baseline_std": self.baseline.std,
            "baseline_complete": self.baseline.complete,
            "samples": self.samples_seen,
            "best_beat": best.beat if best else None,
            "best_score": best.score if best else None,
            "final_offset": self.offset,
            "curve": curve,
            "candidates_scored": len(scored),
            "converged": self.converged,
            "response_z": self.response_z,
        }
