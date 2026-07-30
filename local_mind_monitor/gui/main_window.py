"""Main window: modern dark chrome, connect/record controls, signal-quality
row, live band-power dB readout, and the scrolling plots."""

from __future__ import annotations

import logging
import os
from datetime import datetime

from PySide6 import QtCore, QtWidgets

from ..device import MuseDevice
from .band_readout import BandReadout
from .plots import BandPowerPlot, RawEEGPlot

log = logging.getLogger("local_mind_monitor.gui")

HSI_COLORS = {"good": "#4caf6a", "ok": "#e0a53a", "bad": "#e05a5a"}

STYLESHEET = """
QMainWindow, QWidget { background: #131317; color: #e6e6ea;
    font-family: 'Segoe UI', 'Segoe UI Variable', sans-serif; font-size: 13px; }

QPushButton { background: #262630; color: #e8e8ee; border: 1px solid #35353f;
    border-radius: 8px; padding: 7px 16px; font-weight: 600; }
QPushButton:hover { background: #30303c; }
QPushButton:pressed { background: #1e1e26; }
QPushButton:disabled { background: #1b1b21; color: #565660; border: 1px solid #24242c; }

QPushButton#connectBtn:enabled { background: #1f7a4d; border: 1px solid #2c9a61; color: #eafff2; }
QPushButton#connectBtn:enabled:hover { background: #248c58; }

QPushButton#disconnectBtn:enabled { background: #8a2f33; border: 1px solid #ad3b40; color: #ffeceb; }
QPushButton#disconnectBtn:enabled:hover { background: #9c363b; }

QPushButton#recordBtn[recording="true"]:enabled { background: #8a2f33; border: 1px solid #ad3b40; color: #ffeceb; }
QPushButton#recordBtn[recording="true"]:enabled:hover { background: #9c363b; }

QLabel#sectionLabel { color: #7d7d88; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
QLabel#vitalLabel { color: #b9b9c4; font-size: 13px; font-weight: 600; }

QStatusBar { background: #0e0e12; color: #9a9aa5; }
QStatusBar::item { border: none; }
"""


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        synthetic: bool = False,
        mac_address: str | None = None,
        low_latency: bool = False,
        preset: str = "p1035",
    ):
        super().__init__()
        self._synthetic = synthetic
        self._mac = mac_address
        self._low_latency = low_latency
        self._preset = preset
        self.device: MuseDevice | None = None

        self.setWindowTitle("EEG Visualizer for Muse" + (" (synthetic)" if synthetic else ""))
        self.resize(1040, 800)
        self.setStyleSheet(STYLESHEET)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 8)
        root.setSpacing(10)

        root.addLayout(self._build_toolbar())
        self.quality_row = self._build_quality_row()
        root.addLayout(self.quality_row)

        # Always-visible band-power readout (shows "–– dB" until connected).
        self.readout = BandReadout()
        root.addWidget(self.readout)

        # Plots are created on connect (need channel names from the device).
        self.plot_area = QtWidgets.QVBoxLayout()
        self.plot_area.setSpacing(10)
        root.addLayout(self.plot_area, stretch=1)
        self.raw_plot: RawEEGPlot | None = None
        self.band_plot: BandPowerPlot | None = None

        self.status = self.statusBar()
        self.status.showMessage("Disconnected")

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._refresh_tick = 0
        self._was_stalled = False

    # ---------------------------------------------------------------- ui builders
    def _build_toolbar(self) -> QtWidgets.QHBoxLayout:
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)

        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.clicked.connect(self._connect)

        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("disconnectBtn")
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.disconnect_btn.setEnabled(False)

        self.record_btn = QtWidgets.QPushButton("Start Recording")
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.clicked.connect(self._toggle_record)
        self.record_btn.setEnabled(False)

        self.marker_btn = QtWidgets.QPushButton("Marker")
        self.marker_btn.setObjectName("markerBtn")
        self.marker_btn.clicked.connect(self._add_marker)
        self.marker_btn.setEnabled(False)

        bar.addWidget(self.connect_btn)
        bar.addWidget(self.disconnect_btn)
        bar.addSpacing(12)
        bar.addWidget(self.record_btn)
        bar.addWidget(self.marker_btn)
        bar.addStretch(1)

        # Live vitals, right-aligned.
        self.hr_lbl = QtWidgets.QLabel("♥ -- BPM")
        self.hr_lbl.setObjectName("vitalLabel")
        self.battery_lbl = QtWidgets.QLabel("Battery --%")
        self.battery_lbl.setObjectName("vitalLabel")
        bar.addWidget(self.hr_lbl)
        bar.addSpacing(14)
        bar.addWidget(self.battery_lbl)
        return bar

    def _reset_vitals(self) -> None:
        self.hr_lbl.setText("♥ -- BPM")
        self.battery_lbl.setText("Battery --%")

    def _build_quality_row(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        label = QtWidgets.QLabel("SIGNAL")
        label.setObjectName("sectionLabel")
        row.addWidget(label)
        self.quality_labels: dict[str, QtWidgets.QLabel] = {}
        row.addStretch(1)
        return row

    def _populate_quality_labels(self, names: list[str]) -> None:
        for name in names:
            lbl = QtWidgets.QLabel(f" {name} ")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet(
                "background:#3a3a44;color:#cfcfd6;border-radius:6px;padding:3px 9px;font-weight:600;"
            )
            self.quality_labels[name] = lbl
            self.quality_row.insertWidget(self.quality_row.count() - 1, lbl)

    def _set_connected_state(self, connected: bool) -> None:
        """Reflect connection state in the controls -- Connect greys out once
        you're connected, Disconnect lights up, and vice versa."""
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        self.record_btn.setEnabled(connected)
        self.marker_btn.setEnabled(connected)

    def _set_recording_style(self, recording: bool) -> None:
        self.record_btn.setProperty("recording", "true" if recording else "false")
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)

    # ---------------------------------------------------------------- actions
    def _connect(self) -> None:
        if self.device is not None:
            return
        self.connect_btn.setEnabled(False)
        self.status.showMessage("Connecting…")
        QtWidgets.QApplication.processEvents()
        try:
            self.device = MuseDevice(
                synthetic=self._synthetic,
                mac_address=self._mac,
                low_latency=self._low_latency,
                preset=self._preset,
            )
            self.device.start()
        except Exception as exc:
            log.exception("Connection failed")
            self.device = None
            self.connect_btn.setEnabled(True)
            self.status.showMessage(f"Connection failed: {exc}")
            QtWidgets.QMessageBox.critical(self, "Connection failed", str(exc))
            return

        names = self.device.channel_names
        self.raw_plot = RawEEGPlot(names)
        self.band_plot = BandPowerPlot(names)
        self.plot_area.addWidget(self.raw_plot, stretch=1)
        self.plot_area.addWidget(self.band_plot, stretch=1)
        self._populate_quality_labels(names)
        self._set_connected_state(True)
        self._refresh_tick = 0
        self._was_stalled = False
        self._timer.start(100)  # 10 Hz UI refresh
        self.status.showMessage(f"Connected @ {self.device.sampling_rate} Hz")

    def _disconnect(self) -> None:
        if self.device is None:
            return
        self._timer.stop()
        if self.device.is_recording:
            self.device.stop_recording()
        self.device.stop()
        self.device = None
        self._teardown_view()
        self.readout.reset()
        self._reset_vitals()
        self.record_btn.setText("Start Recording")
        self._set_recording_style(False)
        self._set_connected_state(False)
        self.status.showMessage("Disconnected")

    def _teardown_view(self) -> None:
        """Remove the plots and signal-quality chips so a reconnect starts
        clean instead of stacking a second set on top of the old one."""
        for plot in (self.raw_plot, self.band_plot):
            if plot is not None:
                self.plot_area.removeWidget(plot)
                plot.deleteLater()
        self.raw_plot = None
        self.band_plot = None
        for lbl in self.quality_labels.values():
            self.quality_row.removeWidget(lbl)
            lbl.deleteLater()
        self.quality_labels.clear()

    def _toggle_record(self) -> None:
        if not self.device:
            return
        if not self.device.is_recording:
            fname = f"session_{datetime.now():%Y%m%d_%H%M%S}.csv"
            path = os.path.join(os.getcwd(), fname)
            self.device.start_recording(path)
            self.record_btn.setText("⏹ Stop Recording")
            self._set_recording_style(True)
            self.status.showMessage(f"Recording → {path}")
        else:
            self.device.stop_recording()
            self.record_btn.setText("Start Recording")
            self._set_recording_style(False)
            self.status.showMessage("Recording stopped")

    def _add_marker(self) -> None:
        if self.device and self.device.is_recording:
            label, ok = QtWidgets.QInputDialog.getText(self, "Marker", "Label:", text="marker")
            if ok:
                self.device.add_marker(label)
                self.status.showMessage(f"Marker: {label}")
        elif self.device:
            self.status.showMessage("Start recording first to drop a marker.")

    # ---------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        if not self.device:
            return
        self._refresh_tick += 1

        eeg = self.device.snapshot(seconds=4.0)
        if self.raw_plot:
            self.raw_plot.update_data(eeg)

        # Band powers and signal quality are FFT-heavy. Running them at the full
        # 10 Hz refresh rate loads the GUI thread needlessly; ~2 Hz is plenty.
        if self._refresh_tick % 5 == 0:
            db = self.device.band_power_db()
            self.readout.update_values(db)
            if self.band_plot:
                self.band_plot.update_data(db)
            self._update_quality()
            self._update_vitals()

        self._update_stream_status()

    def _update_vitals(self) -> None:
        bat = self.device.battery_level()
        hr = self.device.heart_rate()
        self.battery_lbl.setText("Battery --%" if bat is None else f"Battery {bat:.0f}%")
        self.hr_lbl.setText("♥ -- BPM" if hr is None else f"♥ {hr:.0f} BPM")

    def _update_quality(self) -> None:
        for name, quality in zip(self.device.channel_names, self.device.signal_quality()):
            lbl = self.quality_labels.get(name)
            if lbl:
                color = HSI_COLORS.get(quality, "#3a3a44")
                lbl.setStyleSheet(
                    f"background:{color};color:white;border-radius:6px;padding:3px 9px;font-weight:600;"
                )

    def _update_stream_status(self) -> None:
        """Tell the user when the Muse stream stalls or drops, instead of
        leaving the plots frozen with no explanation."""
        dev = self.device
        if dev is None:
            return
        if dev.stream_lost():
            # Recovery never stops now, so don't tell the user to do it by hand.
            msg = (f"⚠ Signal lost ({dev.seconds_since_data():.0f}s) — still reconnecting "
                   f"automatically (attempt {dev.recovery_attempts()}). "
                   "Check the headband is on and charged.")
            if self.status.currentMessage() != msg:
                self.status.showMessage(msg)
            self._was_stalled = True
        elif dev.is_stalled():
            self.status.showMessage(
                f"⚠ Signal stalled ({dev.seconds_since_data():.0f}s) — "
                "check the headband; reconnecting…"
            )
            self._was_stalled = True
        elif self._was_stalled:
            # Recovered -- restore the normal status line.
            if dev.is_recording:
                self.status.showMessage("Recording…")
            else:
                self.status.showMessage(f"Connected @ {dev.sampling_rate} Hz")
            self._was_stalled = False

    def closeEvent(self, event) -> None:
        if self.device:
            self.device.stop()
        super().closeEvent(event)
