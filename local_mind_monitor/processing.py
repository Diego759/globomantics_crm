"""EEG signal processing: band powers and signal quality (HSI analog).

Kept free of any GUI dependency so the full acquisition -> processing ->
recording pipeline can run and be tested headless.
"""

from __future__ import annotations

import numpy as np
from brainflow.data_filter import DataFilter, DetrendOperations, WindowOperations

# Band names/symbols/edges live in .bands so the Chamber can import them
# without BrainFlow; re-exported here because this is where callers expect them.
from .bands import BANDS, BAND_RANGES, BAND_SYMBOLS  # noqa: F401


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


def heart_rate(ppg: np.ndarray, sampling_rate: int) -> float | None:
    """Estimate heart rate (BPM) from PPG / optical channels.

    The Muse S Athena exposes 16 raw optical channels (no fixed IR/red mapping),
    so instead of BrainFlow's two-channel ``get_heart_rate`` we find the channel
    with the clearest pulse and read its dominant frequency in the physiological
    band. Returns ``None`` when there's no prominent pulse or too little data
    yet, so the UI can show ``--`` rather than a made-up number.
    """
    if ppg.ndim == 1:
        ppg = ppg[None, :]
    n = ppg.shape[1]
    if n < sampling_rate * 4:  # need a few seconds for a stable estimate
        return None

    lo, hi = 0.7, 3.3  # 42-198 BPM
    freqs = np.fft.rfftfreq(n, d=1.0 / sampling_rate)
    band = (freqs >= lo) & (freqs <= hi)
    if not band.any():
        return None
    band_freqs = freqs[band]
    df = freqs[1] - freqs[0] if freqs.size > 1 else sampling_rate / n
    window = np.hanning(n)

    best_bpm: float | None = None
    best_score = 0.0
    for ch in range(ppg.shape[0]):
        x = ppg[ch].astype(float)
        if not np.all(np.isfinite(x)) or np.std(x) < 1e-9:
            continue
        spec = np.abs(np.fft.rfft((x - x.mean()) * window))[band]
        peak = int(np.argmax(spec))
        # Peak prominence vs the rest of the band -- rejects flat/noisy channels.
        score = spec[peak] / (np.mean(spec) + 1e-12)
        if score <= best_score:
            continue
        # Parabolic interpolation around the peak for sub-bin accuracy.
        freq = band_freqs[peak]
        if 0 < peak < spec.size - 1:
            a, b, c = spec[peak - 1], spec[peak], spec[peak + 1]
            denom = a - 2 * b + c
            if denom != 0:
                freq += 0.5 * (a - c) / denom * df
        best_score, best_bpm = score, float(freq * 60.0)

    if best_bpm is None or best_score < 3.0:
        return None
    return best_bpm


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
