"""pyqtgraph widgets: scrolling raw EEG and per-channel band powers."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtWidgets

from ..processing import BANDS

CHANNEL_COLORS = ["#e15759", "#4e79a7", "#59a14f", "#f28e2b", "#b07aa1", "#76b7b2"]
BAND_COLORS = ["#4e79a7", "#59a14f", "#f28e2b", "#e15759", "#b07aa1"]


class RawEEGPlot(QtWidgets.QWidget):
    """One stacked line per electrode, scrolling left to right."""

    def __init__(self, channel_names: list[str]):
        super().__init__()
        self.channel_names = channel_names
        self.offset = 200.0  # vertical spacing between stacked channels (uV)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget(title="Raw EEG (uV)")
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=False, y=True, alpha=0.2)
        self.plot.getAxis("left").setTicks(
            [[(i * self.offset, name) for i, name in enumerate(channel_names)]]
        )
        layout.addWidget(self.plot)
        self.curves = []
        for i, name in enumerate(channel_names):
            pen = pg.mkPen(CHANNEL_COLORS[i % len(CHANNEL_COLORS)], width=1)
            self.curves.append(self.plot.plot(pen=pen))

    def update_data(self, eeg: np.ndarray) -> None:
        if eeg.shape[1] == 0:
            return
        x = np.arange(eeg.shape[1])
        for i, curve in enumerate(self.curves):
            trace = eeg[i] - np.mean(eeg[i])
            curve.setData(x, trace + i * self.offset)


class BandPowerPlot(QtWidgets.QWidget):
    """Grouped bar chart: five bands per electrode."""

    def __init__(self, channel_names: list[str]):
        super().__init__()
        self.channel_names = channel_names
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget(title="Absolute Band Power")
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=False, y=True, alpha=0.2)
        n_ch = len(channel_names)
        group_w = 0.8
        self.bar_w = group_w / len(BANDS)
        # x tick labels: one group per channel
        ticks = [[(i, name) for i, name in enumerate(channel_names)]]
        self.plot.getAxis("bottom").setTicks(ticks)
        self.plot.addLegend(offset=(-10, 10))
        self.bars = []
        for b, band in enumerate(BANDS):
            bar = pg.BarGraphItem(
                x=np.arange(n_ch) + (b - len(BANDS) / 2) * self.bar_w + self.bar_w / 2,
                height=np.zeros(n_ch),
                width=self.bar_w,
                brush=BAND_COLORS[b],
                name=band,
            )
            self.plot.addItem(bar)
            self.bars.append(bar)

    def update_data(self, band_powers: dict[int, np.ndarray]) -> None:
        n_ch = len(self.channel_names)
        for b in range(len(BANDS)):
            heights = np.array(
                [float(band_powers.get(ch, np.zeros(len(BANDS)))[b]) for ch in range(n_ch)]
            )
            self.bars[b].setOpts(height=heights)
