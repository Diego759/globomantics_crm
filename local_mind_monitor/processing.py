"""EEG signal processing: band powers and signal quality (HSI analog).

Kept free of any GUI dependency so the full acquisition -> processing ->
recording pipeline can run and be tested headless.
"""

from __future__ import annotations

import numpy as np
from brainflow.data_filter import DataFilter

# Band names in the order BrainFlow's get_avg_band_powers returns them.
# BrainFlow bands: 1-4, 4-8, 8-13, 13-30, 30-50 Hz. These line up with Mind
# Monitor's Delta/Theta/Alpha/Beta/Gamma (Mind Monitor uses alpha 7.5-13 and
# gamma 30-44; the small edge differences are not meaningful for a live view).
BANDS = ("Delta", "Theta", "Alpha", "Beta", "Gamma")


def compute_band_powers(eeg: np.ndarray, channels: list[int], sampling_rate: int) -> dict[int, np.ndarray]:
    """Return {channel_index: array of 5 band powers} for the given channels.

    ``eeg`` is a 2d BrainFlow data array (rows = channels). Band powers are
    computed per channel (get_avg_band_powers averages across the channels it
    is given, so we call it one channel at a time to keep them separate).
    Returns zeros for a channel if there is not enough data yet.
    """
    result: dict[int, np.ndarray] = {}
    # get_avg_band_powers needs a couple of seconds of data for a stable PSD.
    min_samples = sampling_rate * 2
    for ch in channels:
        if eeg.shape[1] < min_samples:
            result[ch] = np.zeros(len(BANDS))
            continue
        try:
            avg, _ = DataFilter.get_avg_band_powers(eeg, [ch], sampling_rate, True)
            result[ch] = np.asarray(avg, dtype=float)
        except Exception:
            result[ch] = np.zeros(len(BANDS))
    return result


def signal_quality(channel_samples: np.ndarray) -> str:
    """Classify one electrode's recent samples as good/ok/bad (HSI analog).

    Mirrors Mind Monitor's horseshoe indicator: a well-seated electrode shows
    EEG-scale variance; a flat trace means no contact, a railed/huge-variance
    trace means a bad connection or motion artifact.
    """
    if channel_samples.size == 0:
        return "bad"
    std = float(np.std(channel_samples))
    # Microvolt-scale heuristics. Muse EEG typically sits in the tens of uV.
    if std < 1.0:
        return "bad"  # flatlined -- no skin contact
    if std > 350.0:
        return "bad"  # railed / heavy artifact
    if std < 5.0 or std > 200.0:
        return "ok"
    return "good"
