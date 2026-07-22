"""EEG signal processing: band powers and signal quality (HSI analog).

Kept free of any GUI dependency so the full acquisition -> processing ->
recording pipeline can run and be tested headless.
"""

from __future__ import annotations

import numpy as np
from brainflow.data_filter import DataFilter, DetrendOperations, WindowOperations

# Band names in the order BrainFlow's get_avg_band_powers returns them.
# BrainFlow bands: 1-4, 4-8, 8-13, 13-30, 30-50 Hz. These line up with Mind
# Monitor's Delta/Theta/Alpha/Beta/Gamma (Mind Monitor uses alpha 7.5-13 and
# gamma 30-44; the small edge differences are not meaningful for a live view).
BANDS = ("Delta", "Theta", "Alpha", "Beta", "Gamma")
# Greek symbols shown in the readout / on the chart, aligned with BANDS.
BAND_SYMBOLS = ("δ", "θ", "α", "β", "γ")  # δ θ α β γ
# Frequency edges (Hz) for each band, aligned with BANDS.
BAND_RANGES = ((1.0, 4.0), (4.0, 8.0), (8.0, 13.0), (13.0, 30.0), (30.0, 50.0))


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


def band_power_db(eeg: np.ndarray, channels: list[int], sampling_rate: int) -> np.ndarray:
    """Absolute band power per band (averaged across ``channels``), in dB.

    Unlike :func:`compute_band_powers` (which returns BrainFlow's normalised,
    sum-to-one *relative* powers), this integrates the Welch PSD over each band
    to get absolute power, then reports ``10*log10(power)`` -- the dB-style
    readout Mind Monitor shows. Returns an array of ``len(BANDS)`` values, NaN
    where there isn't enough data yet.
    """
    n = eeg.shape[1]
    if n < sampling_rate:  # need ~1 s for a usable PSD
        return np.full(len(BANDS), np.nan)

    nfft = DataFilter.get_nearest_power_of_two(min(n, sampling_rate * 2))
    while nfft > n and nfft > 16:
        nfft //= 2
    if nfft < 16:
        return np.full(len(BANDS), np.nan)

    totals = np.zeros(len(BANDS))
    used = 0
    for ch in channels:
        sig = np.ascontiguousarray(eeg[ch], dtype=np.float64).copy()
        try:
            DataFilter.detrend(sig, DetrendOperations.LINEAR.value)
            psd = DataFilter.get_psd_welch(
                sig, nfft, nfft // 2, sampling_rate, WindowOperations.HANNING.value
            )
            for b, (lo, hi) in enumerate(BAND_RANGES):
                totals[b] += DataFilter.get_band_power(psd, lo, hi)
            used += 1
        except Exception:
            continue

    if used == 0:
        return np.full(len(BANDS), np.nan)
    totals /= used
    with np.errstate(divide="ignore"):
        return 10.0 * np.log10(np.maximum(totals, 1e-12))


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
