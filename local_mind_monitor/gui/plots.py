"""pyqtgraph widgets: scrolling raw EEG and a Mind Monitor-style scrolling
band-power (dB) chart with a Greek symbol riding the end of each band's line."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtGui, QtWidgets

from ..processing import BAND_SYMBOLS, BANDS

# Dark theme to match the app chrome.
pg.setConfigOptions(antialias=True, background="#15151a", foreground="#8b8b95")

# Vivid per-band palette (Delta, Theta, Alpha, Beta, Gamma) echoing the
# familiar Mind Monitor colouring: red / purple / cyan / green / orange.
BAND_COLORS = ["#ff5b6a", "#c77dff", "#4cc9f0", "#5fd35f", "#ffa94d"]
CHANNEL_COLORS = ["#ff6b6b", "#4dabf7", "#69db7c", "#ffd43b", "#da77f2", "#3bc9db"]


class RawEEGPlot(QtWidgets.QWidget):
    """One stacked line per electrode, scrolling left to right."""

    def __init__(self, channel_names: list[str]):
        super().__init__()
        self.channel_names = channel_names
        self.offset = 200.0  # vertical spacing between stacked channels (uV)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget()
        self.plot.setTitle("Raw EEG", color="#cfcfd6", size="10pt")
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.showGrid(x=False, y=True, alpha=0.12)
        self.plot.getAxis("left").setTicks(
            [[(i * self.offset, name) for i, name in enumerate(channel_names)]]
        )
        self.plot.getAxis("bottom").setStyle(showValues=False)
        layout.addWidget(self.plot)
        self.curves = []
        for i, name in enumerate(channel_names):
            pen = pg.mkPen(CHANNEL_COLORS[i % len(CHANNEL_COLORS)], width=1.2)
            self.curves.append(self.plot.plot(pen=pen))

    def update_data(self, eeg: np.ndarray) -> None:
        if eeg.shape[1] == 0:
            return
        x = np.arange(eeg.shape[1])
        for i, curve in enumerate(self.curves):
            trace = eeg[i] - np.mean(eeg[i])
            curve.setData(x, trace + i * self.offset)


class BandPowerPlot(QtWidgets.QWidget):
    """Scrolling line per band (absolute power in dB), with each band's Greek
    symbol tracking the right-hand end of its line -- like Mind Monitor."""

    HISTORY = 240  # points kept (~2 min at the GUI's ~2 Hz band update rate)

    def __init__(self, channel_names: list[str] | None = None):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget()
        self.plot.setTitle("Absolute Band Power (dB)", color="#cfcfd6", size="10pt")
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.showGrid(x=False, y=True, alpha=0.12)
        self.plot.setLabel("left", "dB")
        self.plot.getAxis("bottom").setStyle(showValues=False)
        self.plot.setXRange(0, self.HISTORY - 1, padding=0.02)
        layout.addWidget(self.plot)

        self.n = len(BANDS)
        self.history = np.full((self.n, self.HISTORY), np.nan)
        self._x = np.arange(self.HISTORY)
        symbol_font = QtGui.QFont("Segoe UI", 13)
        symbol_font.setBold(True)
        self.curves = []
        self.markers = []
        for b in range(self.n):
            pen = pg.mkPen(BAND_COLORS[b], width=2)
            # connect="finite" leaves gaps for NaN (before data has filled in).
            self.curves.append(self.plot.plot(pen=pen, connect="finite"))
            marker = pg.TextItem(BAND_SYMBOLS[b], color=BAND_COLORS[b], anchor=(0, 0.5))
            marker.setFont(symbol_font)
            self.plot.addItem(marker)
            self.markers.append(marker)

    def update_data(self, db: np.ndarray) -> None:
        db = np.asarray(db, dtype=float)
        self.history = np.roll(self.history, -1, axis=1)
        self.history[:, -1] = db[: self.n]
        for b in range(self.n):
            self.curves[b].setData(self._x, self.history[b])
            last = self.history[b, -1]
            if np.isfinite(last):
                self.markers[b].setPos(self.HISTORY - 1, float(last))
