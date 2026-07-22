"""A row of live, colour-coded band-power cards (δ Delta … γ Gamma), each
showing that band's current absolute power in dB -- the headline readout,
mirroring Mind Monitor's coloured band values."""

from __future__ import annotations

import math

from PySide6 import QtCore, QtWidgets

from ..processing import BAND_SYMBOLS, BANDS
from .plots import BAND_COLORS


class _BandCard(QtWidgets.QFrame):
    def __init__(self, name: str, symbol: str, color: str):
        super().__init__()
        self.setObjectName("bandCard")
        self.setStyleSheet(
            "#bandCard {"
            " background: rgba(255,255,255,0.035);"
            f" border-left: 3px solid {color};"
            " border-top-left-radius: 4px; border-bottom-left-radius: 4px;"
            " border-top-right-radius: 8px; border-bottom-right-radius: 8px; }"
        )
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(1)

        head = QtWidgets.QLabel(f"{symbol}  {name}")
        head.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        self.value = QtWidgets.QLabel("–– dB")
        self.value.setStyleSheet(
            "color: #f2f2f5; font-size: 20px; font-weight: 700; background: transparent;"
        )
        lay.addWidget(head)
        lay.addWidget(self.value)

    def set_value(self, v: float) -> None:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            self.value.setText("–– dB")
        else:
            self.value.setText(f"{v:.1f} dB")


class BandReadout(QtWidgets.QWidget):
    """Row of five band cards; call :meth:`update_values` with the dB array."""

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.cards: list[_BandCard] = []
        for i, band in enumerate(BANDS):
            card = _BandCard(band, BAND_SYMBOLS[i], BAND_COLORS[i])
            lay.addWidget(card)
            self.cards.append(card)

    def update_values(self, db) -> None:
        for i, card in enumerate(self.cards):
            card.set_value(db[i] if db is not None and i < len(db) else float("nan"))

    def reset(self) -> None:
        for card in self.cards:
            card.set_value(float("nan"))
