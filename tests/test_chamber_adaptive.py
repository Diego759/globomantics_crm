"""The closed loop: it must find a real peak, and refuse to learn from junk."""

from __future__ import annotations

import numpy as np
import pytest

from local_mind_monitor.chamber.adaptive import (
    MIN_SCORED_SAMPLES,
    AdaptiveController,
    Baseline,
)

SAMPLE_INTERVAL = 0.5  # what the live feeder pushes in at


class FakeBrain:
    """Relative band powers whose alpha peaks at one frequency, plus noise."""

    def __init__(self, peak: float, width: float = 0.5, strength: float = 0.05, seed: int = 0):
        self.peak, self.width, self.strength = peak, width, strength
        self.rng = np.random.default_rng(seed)

    def rest(self) -> np.ndarray:
        """A sample with no tone playing -- what the quiet baseline measures."""
        return self.sample(beat=self.peak + 1e6)

    def sample(self, beat: float) -> np.ndarray:
        response = np.exp(-((beat - self.peak) ** 2) / (2 * self.width ** 2))
        rel = np.array([0.30, 0.20, 0.22 + self.strength * response, 0.18, 0.10])
        rel = np.maximum(rel + self.rng.normal(0, 0.004, 5), 1e-6)
        return rel / rel.sum()


def rest_baseline(brain: FakeBrain, samples: int = 120) -> Baseline:
    """A baseline measured in silence, the way ChamberSession collects it."""
    return Baseline.from_samples(2, [brain.rest() for _ in range(samples)])


def run(controller: AdaptiveController, brain: FakeBrain, minutes: float,
        nominal: float = 10.0, quality_ok: bool = True) -> float:
    t, beat = 0.0, nominal
    while t < minutes * 60:
        beat = controller.update(t, brain.sample(beat), nominal_beat=nominal, quality_ok=quality_ok)
        t += SAMPLE_INTERVAL
    return beat


def test_plays_the_nominal_beat_until_the_baseline_is_in():
    c = AdaptiveController("Alpha", 10.0)
    brain = FakeBrain(9.2)
    for i in range(30):  # 15 s -- nowhere near the 60 s baseline
        assert c.update(i * SAMPLE_INTERVAL, brain.sample(10.0), nominal_beat=10.0) == 10.0
    assert not c.searching


@pytest.mark.parametrize("peak", [9.2, 10.0, 10.8])
def test_search_converges_on_the_frequency_that_actually_responded(peak):
    brain = FakeBrain(peak)
    c = AdaptiveController("Alpha", 10.0, baseline=rest_baseline(brain))
    final = run(c, brain, minutes=25)
    report = c.report()
    assert report["best_beat"] == pytest.approx(peak, abs=0.21)
    assert final == pytest.approx(peak, abs=0.21)
    assert report["converged"]
    assert report["best_score"] > 1.0


def test_stays_put_when_nothing_responds():
    """A brain that ignores the tones should not send the search wandering off."""
    brain = FakeBrain(peak=10.0, strength=0.0)
    c = AdaptiveController("Alpha", 10.0, baseline=rest_baseline(brain))
    run(c, brain, minutes=25)
    assert abs(c.report()["final_offset"]) <= c.span
    best = c.report()["best_score"]
    assert abs(best) < 1.0  # no response worth calling a response


def test_bad_signal_quality_freezes_the_loop():
    c = AdaptiveController("Alpha", 10.0)
    brain = FakeBrain(9.2)
    run(c, brain, minutes=25, quality_ok=False)
    assert c.holding
    assert c.samples_seen == 0
    assert not c.baseline.complete
    assert c.beat_now() == 10.0


def test_baseline_z_is_in_standard_deviations():
    base = Baseline.from_samples(2, [np.array([0.3, 0.2, 0.2, 0.2, 0.1]) for _ in range(10)])
    assert base.complete
    assert base.mean == pytest.approx(0.2)
    assert base.std >= 1e-3  # floored, so a flat baseline cannot explode the z
    louder = np.array([0.3, 0.2, 0.2 + 3 * base.std, 0.2, 0.1])
    assert base.z(louder) == pytest.approx(3.0, abs=0.01)


def test_baseline_from_samples_can_serve_every_band():
    samples = [np.array([0.3, 0.2, 0.2, 0.2, 0.1]) for _ in range(8)]
    for index in range(5):
        assert Baseline.from_samples(index, samples).complete


def test_controller_rejects_a_baseline_for_the_wrong_band():
    wrong = Baseline.from_samples(1, [np.array([0.3, 0.2, 0.2, 0.2, 0.1])] * 8)
    with pytest.raises(ValueError):
        AdaptiveController("Alpha", 10.0, baseline=wrong)


def test_unknown_band_is_refused():
    with pytest.raises(ValueError):
        AdaptiveController("Psi", 10.0)


def test_report_curve_covers_every_candidate_tried():
    brain = FakeBrain(9.6)
    c = AdaptiveController("Alpha", 10.0, baseline=rest_baseline(brain))
    run(c, brain, minutes=25)
    curve = c.report()["curve"]
    assert len(curve) >= 3
    assert curve == sorted(curve, key=lambda row: row[0])
    assert all(n >= MIN_SCORED_SAMPLES for _, _, n in curve)
    # Every tested frequency stays inside the search window around the nominal.
    assert all(abs(beat - 10.0) <= c.span + 1e-6 for beat, _, _ in curve)


def test_never_searches_outside_its_span():
    brain = FakeBrain(20.0)  # a peak it can never reach: pulls hard at the edge
    c = AdaptiveController("Alpha", 10.0, span=1.0, step=0.4, baseline=rest_baseline(brain))
    beats = []
    t = 0.0
    beat = 10.0
    while t < 40 * 60:
        beat = c.update(t, brain.sample(beat), nominal_beat=10.0)
        beats.append(beat)
        t += SAMPLE_INTERVAL
    assert max(beats) <= 11.0 + 1e-6
    assert min(beats) >= 9.0 - 1e-6


def test_nan_input_is_ignored():
    c = AdaptiveController("Alpha", 10.0)
    bad = np.array([np.nan] * 5)
    assert c.update(1.0, bad, nominal_beat=10.0) == 10.0
    assert c.samples_seen == 0
