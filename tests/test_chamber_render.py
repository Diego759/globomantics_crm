"""WAV export: the file has to be a real, correct, playable-anywhere WAV."""

from __future__ import annotations

import wave

import numpy as np
import pytest

from local_mind_monitor.chamber import journeys as J
from local_mind_monitor.chamber.cli import main
from local_mind_monitor.chamber.render import estimate_bytes, render_journey

SR = 16000  # low rate keeps the tests quick; the carriers here stay well under Nyquist


def read_wav(path):
    with wave.open(str(path)) as wav:
        assert wav.getnchannels() == 2
        assert wav.getsampwidth() == 2
        frames = wav.getnframes()
        rate = wav.getframerate()
        data = np.frombuffer(wav.readframes(frames), dtype="<i2").reshape(-1, 2) / 32768.0
    return data, rate


def peak_frequency(signal: np.ndarray, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(signal.size)))
    return float(np.fft.rfftfreq(signal.size, 1.0 / rate)[int(np.argmax(spectrum))])


def test_render_writes_a_wav_of_the_right_length(tmp_path):
    journey = J.custom(beat=10.0, minutes=0.5, carrier=200.0)
    path = render_journey(journey, tmp_path / "out.wav", sample_rate=SR)
    data, rate = read_wav(path)
    assert rate == SR
    assert len(data) == pytest.approx(journey.seconds * SR, rel=1e-3)
    assert path.stat().st_size == pytest.approx(estimate_bytes(journey, SR), rel=1e-3)


def test_rendered_tones_are_the_frequencies_the_journey_asked_for(tmp_path):
    path = render_journey(J.custom(beat=8.0, minutes=0.5, carrier=180.0),
                          tmp_path / "out.wav", sample_rate=SR)
    data, rate = read_wav(path)
    middle = data[len(data) // 3: len(data) // 3 + rate * 4]
    assert peak_frequency(middle[:, 0], rate) == pytest.approx(176.0, abs=1.0)
    assert peak_frequency(middle[:, 1], rate) == pytest.approx(184.0, abs=1.0)


def test_tuning_offsets_shift_the_beat_not_the_carrier(tmp_path):
    """The point of --tune: bake in the frequency your own EEG responded to."""
    journey = J.custom(beat=10.0, minutes=0.5, carrier=200.0)
    path = render_journey(journey, tmp_path / "tuned.wav", sample_rate=SR,
                          beat_offsets={"Alpha": -1.2})
    data, rate = read_wav(path)
    middle = data[len(data) // 3: len(data) // 3 + rate * 8]
    left = peak_frequency(middle[:, 0], rate)
    right = peak_frequency(middle[:, 1], rate)
    assert right - left == pytest.approx(8.8, abs=0.4)
    assert (left + right) / 2 == pytest.approx(200.0, abs=0.5)


def test_offsets_for_other_bands_are_left_alone(tmp_path):
    journey = J.custom(beat=10.0, minutes=0.5, carrier=200.0)   # an Alpha journey
    path = render_journey(journey, tmp_path / "x.wav", sample_rate=SR,
                          beat_offsets={"Theta": -1.5})
    data, rate = read_wav(path)
    middle = data[len(data) // 3: len(data) // 3 + rate * 8]
    beat = peak_frequency(middle[:, 1], rate) - peak_frequency(middle[:, 0], rate)
    assert beat == pytest.approx(10.0, abs=0.4)


def test_render_starts_and_ends_silent_and_never_clips(tmp_path):
    path = render_journey(J.custom(beat=10.0, minutes=0.5), tmp_path / "out.wav", sample_rate=SR)
    data, rate = read_wav(path)
    assert np.abs(data).max() < 1.0
    assert np.abs(data[:64]).max() < 0.01
    assert np.abs(data[-64:]).max() < 0.01


def test_isochronic_journey_renders_identical_channels(tmp_path):
    """Isochronic works on speakers, so both channels must carry the same pulse.

    Rendered without the ambience bed, which is deliberately decorrelated
    between the ears; the remaining difference is one LSB of dither.
    """
    path = render_journey(J.get("open-room"), tmp_path / "iso.wav", sample_rate=SR,
                          ambience_kind="none")
    data, _ = read_wav(path)
    assert np.allclose(data[:, 0], data[:, 1], atol=3 / 32768)
    assert np.abs(data).max() > 0.05


def test_cli_render_writes_a_file(tmp_path, capsys):
    out = tmp_path / "cli.wav"
    code = main(["render", "--beat", "10", "--minutes", "0.5", "--sample-rate", str(SR),
                 "-o", str(out)])
    assert code == 0
    assert out.exists()
    assert "done" in capsys.readouterr().out


def test_cli_list_and_show_run(capsys):
    assert main(["list"]) == 0
    assert "first-descent" in capsys.readouterr().out
    assert main(["show", "deep-delta"]) == 0
    out = capsys.readouterr().out
    assert "Deep Delta" in out and "Delta" in out


def test_cli_reports_unknown_journeys(capsys):
    assert main(["show", "nope"]) == 2
    assert "unknown journey" in capsys.readouterr().err


def test_cli_parses_tuning_offsets():
    from local_mind_monitor.chamber.cli import _parse_offsets

    assert _parse_offsets("Alpha:-0.8,theta:+0.4") == {"Alpha": -0.8, "Theta": 0.4}
    assert _parse_offsets(None) == {}
    with pytest.raises(SystemExit):
        _parse_offsets("Alpha=-0.8")
