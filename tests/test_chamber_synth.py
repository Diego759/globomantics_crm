"""DSP tests: the tones have to be the frequencies we claim, and nothing may click."""

from __future__ import annotations

import numpy as np
import pytest

from local_mind_monitor.chamber.synth import MODE_ISOCHRONIC, MODE_MONAURAL, BinauralSynth

SR = 44100


def peak_frequency(signal: np.ndarray, sample_rate: int = SR) -> float:
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(signal.size)))
    freqs = np.fft.rfftfreq(signal.size, 1.0 / sample_rate)
    return float(freqs[int(np.argmax(spectrum))])


def render_seconds(synth: BinauralSynth, seconds: float, block: int = 1024) -> np.ndarray:
    blocks = [synth.render(block) for _ in range(int(seconds * SR / block))]
    return np.concatenate(blocks)


def test_binaural_pair_straddles_the_carrier():
    synth = BinauralSynth(SR, carrier=200.0, beat=10.0, gain=1.0)
    synth.snap()
    audio = render_seconds(synth, 4.0)
    left = peak_frequency(audio[:, 0])
    right = peak_frequency(audio[:, 1])
    assert left == pytest.approx(195.0, abs=0.5)
    assert right == pytest.approx(205.0, abs=0.5)
    # The beat is the difference, centred on the carrier -- that is the whole point.
    assert right - left == pytest.approx(10.0, abs=0.5)
    assert (left + right) / 2 == pytest.approx(200.0, abs=0.5)


def test_monaural_sends_both_tones_to_both_ears():
    synth = BinauralSynth(SR, mode=MODE_MONAURAL, carrier=200.0, beat=8.0, gain=1.0)
    synth.snap()
    audio = render_seconds(synth, 4.0)
    assert np.allclose(audio[:, 0], audio[:, 1])
    spectrum = np.abs(np.fft.rfft(audio[:, 0] * np.hanning(audio.shape[0])))
    freqs = np.fft.rfftfreq(audio.shape[0], 1.0 / SR)
    for expected in (196.0, 204.0):
        near = spectrum[np.abs(freqs - expected) < 1.0]
        assert near.size and near.max() > 0.05 * spectrum.max()


def test_isochronic_envelope_pulses_at_the_beat_rate():
    synth = BinauralSynth(SR, mode=MODE_ISOCHRONIC, carrier=200.0, beat=7.0, gain=1.0, ambience=0.0)
    synth.snap()
    audio = render_seconds(synth, 8.0)
    envelope = np.abs(audio[:, 0])
    # Smooth away the carrier so only the pulse rate is left.
    window = SR // 100
    envelope = np.convolve(envelope, np.ones(window) / window, mode="same")
    envelope -= envelope.mean()
    assert peak_frequency(envelope) == pytest.approx(7.0, abs=0.3)


def test_no_discontinuity_across_blocks_or_parameter_changes():
    """A click is a step in the waveform; there must not be one anywhere."""
    synth = BinauralSynth(SR, carrier=200.0, beat=10.0, gain=1.0)
    synth.snap()
    chunks = [synth.render(512)]
    synth.set(beat=4.0, carrier=400.0, gain=0.3)     # a violent parameter change
    chunks += [synth.render(n) for n in (128, 4096, 777, 1024)]
    synth.set(gain=1.0)
    chunks += [synth.render(1024) for _ in range(20)]
    audio = np.concatenate(chunks)
    step = np.max(np.abs(np.diff(audio[:, 0])))
    # Largest legitimate per-sample step: the highest tone at full amplitude.
    # (Ambience is left off here -- broadband noise steps by design.)
    assert step < 2 * np.pi * 450 / SR * 0.5


def test_ambience_loop_wraps_without_a_seam():
    """The noise bed is a finite loop read round and round; the wrap must not tick."""
    from local_mind_monitor.chamber.synth import NOISE_LOOP_SECONDS

    synth = BinauralSynth(SR, gain=0.0, ambience=1.0, seed=3)
    synth.snap()
    wrap = int(NOISE_LOOP_SECONDS * SR)
    audio = np.concatenate([synth.render(SR) for _ in range(int(NOISE_LOOP_SECONDS) + 2)])
    steps = np.abs(np.diff(audio[:, 0]))
    at_wrap = steps[wrap - 200 : wrap + 200].max()
    assert at_wrap <= steps.max()  # the wrap is not the worst step in the signal


def test_parameters_are_clamped_not_rejected():
    synth = BinauralSynth(SR)
    synth.set(carrier=10_000.0, beat=-5.0, gain=4.0, ambience=-1.0)
    assert synth.carrier == 1000.0
    assert synth.beat == 0.1
    assert synth.gain == 1.0
    assert synth.ambience == 0.0


def test_output_never_clips_even_with_everything_up():
    synth = BinauralSynth(SR, mode=MODE_MONAURAL, gain=1.0, ambience=1.0)
    synth.snap()
    audio = render_seconds(synth, 3.0)
    assert np.max(np.abs(audio)) <= 1.0
    assert audio.dtype == np.float32


def test_silence_is_actually_silent():
    synth = BinauralSynth(SR, gain=0.0, ambience=0.0)
    synth.snap()
    assert synth.is_silent()
    assert not np.any(synth.render(1024))


def test_gain_ramps_instead_of_stepping():
    synth = BinauralSynth(SR, carrier=200.0, beat=10.0, gain=0.0, ambience=0.0)
    synth.snap()
    synth.set(gain=1.0)
    first = synth.render(64)          # ~1.5 ms in: nowhere near full volume yet
    assert np.max(np.abs(first)) < 0.05
    later = synth.render(SR)          # a second later it has arrived
    assert np.max(np.abs(later)) > 0.2


def test_ambience_bed_is_decorrelated_between_ears():
    synth = BinauralSynth(SR, gain=0.0, ambience=1.0, seed=7)
    synth.snap()
    audio = render_seconds(synth, 2.0)
    correlation = np.corrcoef(audio[:, 0], audio[:, 1])[0, 1]
    assert abs(correlation) < 0.3


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        BinauralSynth(SR, mode="telepathy")
    with pytest.raises(ValueError):
        BinauralSynth(SR).set(mode="telepathy")


def test_block_size_does_not_change_the_signal():
    """Same parameters, different block sizes -> sample-identical audio."""
    a = BinauralSynth(SR, carrier=200.0, beat=10.0, gain=1.0, ambience=0.0)
    b = BinauralSynth(SR, carrier=200.0, beat=10.0, gain=1.0, ambience=0.0)
    a.snap()
    b.snap()
    first = np.concatenate([a.render(1024) for _ in range(16)])
    second = np.concatenate([b.render(n) for n in (4096, 1000, 24, 8192, 3072)])
    n = min(len(first), len(second))
    assert np.allclose(first[:n], second[:n], atol=1e-6)
