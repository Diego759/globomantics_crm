"""Main window: controls, signal-quality row, and the live plots."""

from __future__ import annotations

import os
from datetime import datetime

from PySide6 import QtCore, QtWidgets

from ..device import MuseDevice
from .plots import BandPowerPlot, RawEEGPlot

HSI_COLORS = {"good": "#59a14f", "ok": "#f28e2b", "bad": "#e15759"}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, synthetic: bool = False, mac_address: str | None = None):
        super().__init__()
        self._synthetic = synthetic
        self._mac = mac_address
        self.device: MuseDevice | None = None

        self.setWindowTitle("Local Mind Monitor" + (" (synthetic)" if synthetic else ""))
        self.resize(900, 700)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        root.addLayout(self._build_toolbar())
        self.quality_row = self._build_quality_row()
        root.addLayout(self.quality_row)

        # Plots are created on connect (need channel names from the device).
        self.plot_area = QtWidgets.QVBoxLayout()
        root.addLayout(self.plot_area, stretch=1)
        self.raw_plot: RawEEGPlot | None = None
        self.band_plot: BandPowerPlot | None = None

        self.status = self.statusBar()
        self.status.showMessage("Disconnected")

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh)

    # ---------------------------------------------------------------- ui builders
    def _build_toolbar(self) -> QtWidgets.QHBoxLayout:
        bar = QtWidgets.QHBoxLayout()
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.record_btn = QtWidgets.QPushButton("Start Recording")
        self.record_btn.clicked.connect(self._toggle_record)
        self.record_btn.setEnabled(False)
        self.marker_btn = QtWidgets.QPushButton("Marker")
        self.marker_btn.clicked.connect(self._add_marker)
        self.marker_btn.setEnabled(False)
        bar.addWidget(self.connect_btn)
        bar.addWidget(self.record_btn)
        bar.addWidget(self.marker_btn)
        bar.addStretch(1)
        return bar

    def _build_quality_row(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Signal:"))
        self.quality_labels: dict[str, QtWidgets.QLabel] = {}
        row.addStretch(1)
        return row

    def _populate_quality_labels(self, names: list[str]) -> None:
        for name in names:
            lbl = QtWidgets.QLabel(f" {name} ")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet("background:#888;color:white;border-radius:4px;padding:2px 6px;")
            self.quality_labels[name] = lbl
            self.quality_row.insertWidget(self.quality_row.count() - 1, lbl)

    # ---------------------------------------------------------------- actions
    def _toggle_connect(self) -> None:
        if self.device is None:
            self.status.showMessage("Connecting...")
            QtWidgets.QApplication.processEvents()
            try:
                self.device = MuseDevice(synthetic=self._synthetic, mac_address=self._mac)
                self.device.start()
            except Exception as exc:
                self.device = None
                self.status.showMessage(f"Connection failed: {exc}")
                QtWidgets.QMessageBox.critical(self, "Connection failed", str(exc))
                return
            names = self.device.channel_names
            self.raw_plot = RawEEGPlot(names)
            self.band_plot = BandPowerPlot(names)
            self.plot_area.addWidget(self.raw_plot, stretch=1)
            self.plot_area.addWidget(self.band_plot, stretch=1)
            self._populate_quality_labels(names)
            self.connect_btn.setText("Disconnect")
            self.record_btn.setEnabled(True)
            self.marker_btn.setEnabled(True)
            self._timer.start(100)  # 10 Hz UI refresh
            self.status.showMessage(f"Connected @ {self.device.sampling_rate} Hz")
        else:
            self._timer.stop()
            self.device.stop()
            self.device = None
            self.connect_btn.setText("Connect")
            self.record_btn.setText("Start Recording")
            self.record_btn.setEnabled(False)
            self.marker_btn.setEnabled(False)
            self.status.showMessage("Disconnected")

    def _toggle_record(self) -> None:
        if not self.device:
            return
        if not self.device.is_recording:
            fname = f"session_{datetime.now():%Y%m%d_%H%M%S}.csv"
            path = os.path.join(os.getcwd(), fname)
            self.device.start_recording(path)
            self.record_btn.setText("Stop Recording")
            self.status.showMessage(f"Recording -> {path}")
        else:
            self.device.stop_recording()
            self.record_btn.setText("Start Recording")
            self.status.showMessage("Recording stopped")

    def _add_marker(self) -> None:
        if self.device and self.device.is_recording:
            label, ok = QtWidgets.QInputDialog.getText(self, "Marker", "Label:", text="marker")
            if ok:
                self.device.add_marker(label)
                self.status.showMessage(f"Marker: {label}")

    # ---------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        if not self.device:
            return
        eeg = self.device.snapshot(seconds=4.0)
        if self.raw_plot:
            self.raw_plot.update_data(eeg)
        if self.band_plot:
            self.band_plot.update_data(self.device.band_powers())
        for name, quality in zip(self.device.channel_names, self.device.signal_quality()):
            lbl = self.quality_labels.get(name)
            if lbl:
                color = HSI_COLORS.get(quality, "#888")
                lbl.setStyleSheet(
                    f"background:{color};color:white;border-radius:4px;padding:2px 6px;"
                )

    def closeEvent(self, event) -> None:
        if self.device:
            self.device.stop()
        super().closeEvent(event)
