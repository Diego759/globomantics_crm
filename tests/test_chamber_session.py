"""End-to-end session runtime, driven faster than real time (no audio device)."""

from __future__ import annotations

import csv

import numpy as np
import pytest

from local_mind_monitor.chamber import journeys as J
from local_mind_monitor.chamber.session import PHASE_BASELINE, PHASE_PLAYING, ChamberSession, format_report

SR = 44100
# Tests that only care about timing and control run at a low sample rate: the
# audio is identical in structure and a 30-minute session renders in a second.
FAST_SR = 8000
BLOCK = 4410          # 0.1 s of audio per render call
EEG_INTERVAL = 0.5    # what the live feeder pushes in at


def alpha_sample(beat: float, peak: float = 9.4, rng=None) -> np.ndarray:
    """Relative band powers for a brain whose alpha likes `peak` Hz."""
    response = np.exp(-((beat - peak) ** 2) / (2 * 0.5 ** 2))
    rel = np.array([0.30, 0.20, 0.22 + 0.06 * response, 0.18, 0.10])
    if rng is not None:
        rel = np.maximum(rel + rng.normal(0, 0.004, 5), 1e-6)
    return rel / rel.sum()


def drive(session: ChamberSession, quality_ok: bool = True, peak: float = 9.4,
          feed: bool = True) -> np.ndarray:
    """Run a whole session as fast as the CPU allows; return the audio."""
    rng = np.random.default_rng(0)
    block = max(64, session.sample_rate // 10)
    blocks, next_eeg = [], 0.0
    while not session.finished:
        blocks.append(session.render(block))
        if feed and session.audio_time >= next_eeg:
            session.feed_bands(alpha_sample(session.state().beat, peak, rng), quality_ok=quality_ok)
            next_eeg += EEG_INTERVAL
    return np.concatenate(blocks)


def short_journey(minutes: float = 6.0) -> J.Journey:
    return J.custom(beat=10.0, minutes=minutes, carrier=200.0)


def peak_frequency(signal: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(signal.size)))
    return float(np.fft.rfftfreq(signal.size, 1.0 / SR)[int(np.argmax(spectrum))])


def test_clock_counts_rendered_audio_not_wall_time():
    session = ChamberSession(short_journey(1.0), baseline_seconds=0.0)
    session.render(SR * 3)
    assert session.audio_time == pytest.approx(3.0)
    assert session.journey_t == pytest.approx(3.0)


def test_baseline_phase_plays_no_tones_then_the_journey_starts():
    session = ChamberSession(short_journey(2.0), adaptive=True, baseline_seconds=10.0)
    quiet = np.concatenate([session.render(BLOCK) for _ in range(100)])  # 10 s
    assert session.state().phase == PHASE_BASELINE
    assert session.synth.gain == 0.0
    # Ambience only: no energy anywhere near the tone pair.
    assert not (194 < peak_frequency(quiet[SR:, 0]) < 206)

    session.render(BLOCK)
    assert session.state().phase == PHASE_PLAYING
    playing = np.concatenate([session.render(BLOCK) for _ in range(300)])
    assert peak_frequency(playing[SR:, 0]) == pytest.approx(195.0, abs=1.0)
    assert peak_frequency(playing[SR:, 1]) == pytest.approx(205.0, abs=1.0)


def test_journey_time_excludes_the_baseline():
    session = ChamberSession(short_journey(2.0), sample_rate=FAST_SR, adaptive=True,
                             baseline_seconds=30.0)
    session.render(FAST_SR * 45)
    assert session.audio_time == pytest.approx(45.0)
    assert session.journey_t == pytest.approx(15.0)


def test_closed_loop_tunes_the_beat_and_reports_the_curve():
    journey = short_journey(30.0)
    session = ChamberSession(journey, sample_rate=FAST_SR, adaptive=True, baseline_seconds=60.0)
    drive(session, peak=9.4)
    report = session.stop()
    assert report["adaptive"]
    alpha = report["bands"]["Alpha"]
    assert alpha["baseline_complete"]
    assert alpha["best_beat"] == pytest.approx(9.4, abs=0.25)
    assert alpha["best_score"] > 1.0
    assert len(alpha["curve"]) >= 3
    text = format_report(report)
    assert "best beat" in text and "response curve" in text


def test_open_loop_when_the_signal_was_never_good():
    """No usable rest data must mean 'play it straight', not 'adapt to noise'."""
    session = ChamberSession(short_journey(4.0), sample_rate=FAST_SR, adaptive=True,
                             baseline_seconds=30.0)
    drive(session, quality_ok=False)
    report = session.stop()
    assert not report["adaptive"]
    assert report["bands"] == {}
    assert "open loop" in format_report(report)


def test_a_session_with_no_headband_still_plays():
    session = ChamberSession(short_journey(2.0), adaptive=False, baseline_seconds=60.0)
    audio = drive(session, feed=False)
    assert session.baseline_seconds == 0.0          # no point measuring nothing
    assert np.max(np.abs(audio)) > 0.05
    assert not session.report()["adaptive"]


def test_audio_fades_in_and_out_and_never_clips():
    session = ChamberSession(short_journey(2.0), baseline_seconds=0.0)
    audio = drive(session, feed=False)
    assert np.max(np.abs(audio)) <= 1.0
    assert np.max(np.abs(audio[:200])) < 0.01                    # fade in
    assert np.max(np.abs(audio[-SR // 10:])) < 0.01              # fade out
    assert np.max(np.abs(audio[len(audio) // 2:][:SR])) > 0.05   # loud in between


def test_log_records_every_sample_with_its_frequency(tmp_path):
    log = tmp_path / "chamber.csv"
    session = ChamberSession(short_journey(8.0), sample_rate=FAST_SR, adaptive=True,
                             baseline_seconds=60.0, log_path=log)
    drive(session)
    session.stop()

    rows = list(csv.DictReader(log.open()))
    assert len(rows) > 100
    assert {"Elapsed", "Phase", "Beat_Hz", "Offset_Hz", "Response_z", "Rel_Alpha"} <= set(rows[0])
    phases = {r["Phase"] for r in rows}
    assert PHASE_BASELINE in phases and PHASE_PLAYING in phases
    playing = [r for r in rows if r["Phase"] == PHASE_PLAYING]
    assert all(float(r["Beat_Hz"]) > 0 for r in playing)
    assert any(r["Response_z"] for r in playing)
    assert float(playing[-1]["Elapsed"]) > float(rows[0]["Elapsed"])


def test_malformed_band_vectors_are_ignored():
    session = ChamberSession(short_journey(2.0), adaptive=True, baseline_seconds=10.0)
    session.feed_bands(None)
    session.feed_bands(np.array([1.0, 2.0]))            # wrong length
    session.feed_bands(np.array([np.nan] * 5))          # not finite
    session.render(SR * 11)
    session.render(BLOCK)   # the phase flips at the start of a block, as in playback
    assert session.state().phase == PHASE_PLAYING
    assert not session.adaptive                          # no usable baseline collected


def test_state_snapshot_tracks_the_journey():
    journey = J.get("first-descent")
    session = ChamberSession(journey, sample_rate=FAST_SR, baseline_seconds=0.0)
    session.render(FAST_SR * 60 * 8)   # 8 min in: mid-descent toward theta
    state = session.state()
    assert state.segment == "Descend"
    assert 6.0 < state.beat < 10.0
    assert state.band in ("Theta", "Alpha")
    assert state.remaining == pytest.approx(journey.seconds - 480, abs=1.0)
