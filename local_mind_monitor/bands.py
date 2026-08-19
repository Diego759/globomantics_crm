"""EEG frequency-band definitions, shared by the analyser and the tone engine.

Kept in its own module (with no third-party imports) so that anything needing
the band edges -- including the Chamber, which must run without BrainFlow
installed -- can have them without pulling in the acquisition stack.
"""

from __future__ import annotations

# Band names in the order BrainFlow's get_avg_band_powers returns them.
# BrainFlow bands: 1-4, 4-8, 8-13, 13-30, 30-50 Hz. These line up with Mind
# Monitor's Delta/Theta/Alpha/Beta/Gamma (Mind Monitor uses alpha 7.5-13 and
# gamma 30-44; the small edge differences are not meaningful for a live view).
BANDS = ("Delta", "Theta", "Alpha", "Beta", "Gamma")
# Greek symbols shown in the readout / on the chart, aligned with BANDS.
BAND_SYMBOLS = ("δ", "θ", "α", "β", "γ")  # δ θ α β γ
# Frequency edges (Hz) for each band, aligned with BANDS.
BAND_RANGES = ((1.0, 4.0), (4.0, 8.0), (8.0, 13.0), (13.0, 30.0), (30.0, 50.0))
