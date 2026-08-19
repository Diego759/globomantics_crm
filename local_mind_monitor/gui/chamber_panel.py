"""The Chamber tab: pick a journey, play it, and watch what your EEG does.

The panel owns nothing clever -- ChamberSession does the work. It starts the
audio, pushes band powers in twice a second, and draws what comes back: the
frequency being played, where it sits in the journey, and how far the target
band has moved above your own resting baseline.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from ..chamber import journeys as J
from ..chamber.session import PHASE_BASELINE, ChamberSession, format_report
from .plots import BAND_COLORS

log = logging.getLogger("local_mind_monitor.gui.chamber")

# Where user-written journeys are picked up from, alongside the built-ins.
USER_JOURNEY_DIR = Path.home() / ".eeg-visualizer" / "journeys"

UI_INTERVAL_MS = 100    # redraw rate
EEG_INTERVAL_MS = 500   # how often band powers are pushed into the session

BAND_COLOR = {name: BAND_COLORS[i] for i, name in enumerate(J.BANDS)}


def _mmss(seconds: float) -> str:
    m, s = divmod(int(max(0.0, seconds)), 60)
    return f"{m}:{s:02d}"


class _RenderWorker(QtCore.QThread):
    """Renders a journey to WAV off the GUI thread."""

    progress = QtCore.Signal(int)
    finished_ok = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, journey, path, volume, mode, offsets):
        super().__init__()
        self._args = (journey, path, volume, mode, offsets)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        from ..chamber.render import render_journey

        journey, path, volume, mode, offsets = self._args

        def on_progress(fraction: float) -> None:
            if self._cancelled:
                raise InterruptedError
            self.progress.emit(int(fraction * 100))

        try:
            render_journey(journey, path, volume=volume, mode=mode,
                           beat_offsets=offsets, progress=on_progress)
        except InterruptedError:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            self.failed.emit("cancelled")
        except Exception as exc:
            log.exception("render failed")
            self.failed.emit(str(exc))
        else:
            self.finished_ok.emit(str(path))


class ChamberPanel(QtWidgets.QWidget):
    def __init__(self, device_getter: Callable[[], object | None]):
        super().__init__()
        self._device_getter = device_getter
        self.session: ChamberSession | None = None
        self.player = None
        self._worker: _RenderWorker | None = None
        self._trace: list[tuple[float, float]] = []  # (journey_t, beat actually played)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(10)
        root.addLayout(self._build_picker())
        root.addWidget(self._build_readout())
        root.addWidget(self._build_timeline(), stretch=1)
        root.addLayout(self._build_transport())
        self.report_box = QtWidgets.QPlainTextEdit()
        self.report_box.setReadOnly(True)
        self.report_box.setMaximumHeight(120)
        self.report_box.setPlaceholderText(
            "After a session with the headband on, your response report appears here."
        )
        self.report_box.setStyleSheet(
            "background:#0f0f14;color:#c8c8d2;border:1px solid #26262e;border-radius:8px;"
            "font-family:monospace;font-size:11px;"
        )
        root.addWidget(self.report_box)

        self._ui_timer = QtCore.QTimer(self)
        self._ui_timer.timeout.connect(self._refresh)
        self._eeg_timer = QtCore.QTimer(self)
        self._eeg_timer.timeout.connect(self._feed_eeg)

        self._select_journey(0)

    # ---------------------------------------------------------------- builders
    def _build_picker(self) -> QtWidgets.QVBoxLayout:
        box = QtWidgets.QVBoxLayout()
        box.setSpacing(6)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.journey_box = QtWidgets.QComboBox()
        self.journeys = list(J.BUILTIN) + J.user_journeys(USER_JOURNEY_DIR)
        for journey in self.journeys:
            self.journey_box.addItem(f"{journey.title}   ·   {_mmss(journey.seconds)}")
        self.journey_box.currentIndexChanged.connect(self._select_journey)
        self.journey_box.setMinimumWidth(280)

        self.mode_box = QtWidgets.QComboBox()
        self.mode_box.addItems(["binaural (headphones)", "monaural (speakers)",
                                "isochronic (speakers)"])
        self.mode_box.currentIndexChanged.connect(lambda _: self._draw_timeline())

        self.adaptive_box = QtWidgets.QCheckBox("Steer with my EEG")
        self.adaptive_box.setToolTip(
            "Measure a quiet baseline first, then move the beat frequency toward\n"
            "whatever actually raises the target band for you."
        )
        self.adaptive_box.setChecked(True)

        self.volume = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(75)
        self.volume.setFixedWidth(120)
        self.volume.valueChanged.connect(self._apply_volume)

        row.addWidget(self.journey_box)
        row.addWidget(self.mode_box)
        row.addWidget(self.adaptive_box)
        row.addStretch(1)
        row.addWidget(QtWidgets.QLabel("Volume"))
        row.addWidget(self.volume)
        box.addLayout(row)

        self.summary_lbl = QtWidgets.QLabel()
        self.summary_lbl.setWordWrap(True)
        self.summary_lbl.setStyleSheet("color:#9a9aa5;font-size:12px;")
        box.addWidget(self.summary_lbl)
        return box

    def _build_readout(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QFrame()
        frame.setObjectName("chamberReadout")
        frame.setStyleSheet(
            "#chamberReadout { background: rgba(255,255,255,0.035);"
            " border: 1px solid #26262e; border-radius: 10px; }"
            " QLabel { background: transparent; }"
        )
        grid = QtWidgets.QGridLayout(frame)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(24)

        self.beat_lbl = QtWidgets.QLabel("––")
        self.beat_lbl.setStyleSheet("color:#f2f2f5;font-size:38px;font-weight:700;")
        self.beat_sub = QtWidgets.QLabel("beat frequency")
        self.beat_sub.setStyleSheet("color:#7d7d88;font-size:11px;letter-spacing:1px;")
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(self.beat_lbl)
        left.addWidget(self.beat_sub)
        grid.addLayout(left, 0, 0, 2, 1)

        self.tones_lbl = self._stat(grid, 0, 1, "TONES", "––")
        self.segment_lbl = self._stat(grid, 0, 2, "SEGMENT", "––")
        self.band_lbl = self._stat(grid, 0, 3, "TARGET BAND", "––")
        self.response_lbl = self._stat(grid, 0, 4, "RESPONSE vs REST", "––")

        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setStyleSheet(
            "QProgressBar { background:#1c1c23; border:none; border-radius:3px; }"
            "QProgressBar::chunk { background:#4cc9f0; border-radius:3px; }"
        )
        grid.addWidget(self.progress, 2, 0, 1, 5)

        self.status_lbl = QtWidgets.QLabel("Ready.")
        self.status_lbl.setStyleSheet("color:#9a9aa5;font-size:12px;")
        grid.addWidget(self.status_lbl, 3, 0, 1, 5)
        return frame

    def _stat(self, grid: QtWidgets.QGridLayout, row: int, col: int,
              title: str, value: str) -> QtWidgets.QLabel:
        head = QtWidgets.QLabel(title)
        head.setStyleSheet("color:#7d7d88;font-size:11px;font-weight:700;letter-spacing:1px;")
        val = QtWidgets.QLabel(value)
        val.setStyleSheet("color:#e6e6ea;font-size:16px;font-weight:600;")
        grid.addWidget(head, row, col)
        grid.addWidget(val, row + 1, col)
        return val

    def _build_timeline(self) -> QtWidgets.QWidget:
        self.timeline = pg.PlotWidget()
        self.timeline.setTitle("Journey", color="#cfcfd6", size="10pt")
        self.timeline.setMenuEnabled(False)
        self.timeline.setMouseEnabled(x=False, y=False)
        self.timeline.showGrid(x=True, y=True, alpha=0.12)
        self.timeline.setLabel("left", "Beat (Hz)")
        self.timeline.setLabel("bottom", "Minutes")
        self.plan_curve = self.timeline.plot(pen=pg.mkPen("#4cc9f0", width=2))
        self.actual_curve = self.timeline.plot(pen=pg.mkPen("#ffa94d", width=2))
        self.playhead = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen("#e6e6ea", width=1))
        self.timeline.addItem(self.playhead)
        return self.timeline

    def _build_transport(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.start_btn = QtWidgets.QPushButton("▶  Start")
        self.start_btn.setObjectName("connectBtn")
        self.start_btn.clicked.connect(self._toggle)
        self.export_btn = QtWidgets.QPushButton("Export WAV…")
        self.export_btn.clicked.connect(self._export)
        self.export_btn.setToolTip(
            "Render this journey to a plain WAV file — copy it to a phone and\n"
            "play it anywhere, with your own tuning baked in if you have run a session."
        )
        row.addWidget(self.start_btn)
        row.addWidget(self.export_btn)
        row.addStretch(1)
        self.hint_lbl = QtWidgets.QLabel()
        self.hint_lbl.setStyleSheet("color:#7d7d88;font-size:11px;")
        row.addWidget(self.hint_lbl)
        return row

    # ---------------------------------------------------------------- helpers
    @property
    def journey(self) -> J.Journey:
        return self.journeys[max(0, self.journey_box.currentIndex())]

    def _mode(self) -> str:
        return ("binaural", "monaural", "isochronic")[self.mode_box.currentIndex()]

    def _learned_offsets(self) -> dict[str, float]:
        """Per-band tuning from the last adaptive session on this journey."""
        if self.session is None:
            return {}
        report = self.session.report()
        return {
            band: r["final_offset"]
            for band, r in report.get("bands", {}).items()
            if r.get("best_beat") is not None and abs(r["final_offset"]) > 0.01
        }

    def _select_journey(self, _index: int) -> None:
        journey = self.journey
        caution = f"   ⚠ {journey.caution}" if journey.caution else ""
        self.summary_lbl.setText(f"{journey.summary}{caution}")
        self.mode_box.setCurrentIndex(("binaural", "monaural", "isochronic").index(journey.mode))
        self._trace.clear()
        self._draw_timeline()

    def _draw_timeline(self) -> None:
        journey = self.journey
        times = np.linspace(0.0, journey.seconds, 400)
        beats = [journey.at(t).beat for t in times]
        self.plan_curve.setData(times / 60.0, beats)
        self.actual_curve.setData([], [])
        self.timeline.setXRange(0, journey.seconds / 60.0, padding=0.01)
        low, high = min(beats), max(beats)
        self.timeline.setYRange(max(0.0, low - 2), high + 2, padding=0.02)
        self.playhead.setPos(0)
        if self._mode() == "binaural":
            self.hint_lbl.setText("Headphones required for binaural.")
        else:
            self.hint_lbl.setText("Works on speakers.")

    def _apply_volume(self, value: int) -> None:
        if self.session is not None:
            self.session.volume = value / 100.0

    # ---------------------------------------------------------------- session
    def _toggle(self) -> None:
        if self.session is None:
            self._start()
        else:
            self._stop()

    def _start(self) -> None:
        from ..chamber.player import AudioUnavailable, Player

        device = self._device_getter()
        adaptive = self.adaptive_box.isChecked() and device is not None
        journey = self.journey
        log_path = None
        if adaptive:
            log_path = Path(os.getcwd()) / f"chamber_{datetime.now():%Y%m%d_%H%M%S}.csv"

        self.session = ChamberSession(
            journey,
            volume=self.volume.value() / 100.0,
            mode=self._mode(),
            adaptive=adaptive,
            baseline_seconds=60.0 if adaptive else 0.0,
            log_path=log_path,
        )
        self.player = Player(self.session)
        try:
            self.player.start()
        except AudioUnavailable as exc:
            self.session = None
            self.player = None
            QtWidgets.QMessageBox.warning(
                self, "No audio output",
                f"{exc}\n\nYou can still use “Export WAV…” and play the file elsewhere.",
            )
            return

        self._trace.clear()
        self.report_box.clear()
        self.start_btn.setText("⏹  Stop")
        self.journey_box.setEnabled(False)
        self.mode_box.setEnabled(False)
        self.adaptive_box.setEnabled(False)
        self._ui_timer.start(UI_INTERVAL_MS)
        if adaptive:
            self._eeg_timer.start(EEG_INTERVAL_MS)
        self.status_lbl.setText(
            "Measuring your resting rhythm — sit still, tones start in a minute."
            if adaptive else
            "Playing open loop. Connect the headband and tick “Steer with my EEG” to close the loop."
        )

    def _stop(self) -> None:
        self._ui_timer.stop()
        self._eeg_timer.stop()
        if self.player is not None:
            self.player.stop()
            self.player = None
        if self.session is not None:
            report = self.session.stop()
            self.report_box.setPlainText(format_report(report))
            if report.get("log"):
                self.status_lbl.setText(f"Session log: {report['log']}")
            else:
                self.status_lbl.setText("Session ended.")
        self.start_btn.setText("▶  Start")
        self.journey_box.setEnabled(True)
        self.mode_box.setEnabled(True)
        self.adaptive_box.setEnabled(True)
        self.progress.setValue(0)
        self.beat_lbl.setText("––")

    def _feed_eeg(self) -> None:
        """Push the headband's current band powers into the running session."""
        device = self._device_getter()
        if device is None or self.session is None:
            return
        try:
            powers = device.band_powers()
            if not powers:
                return
            relative = np.mean(np.stack(list(powers.values())), axis=0)
            total = float(np.sum(relative))
            if total <= 0:
                return
            quality = device.signal_quality()
            ok = sum(q in ("good", "ok") for q in quality) >= max(1, len(quality) // 2)
            self.session.feed_bands(relative / total, quality_ok=ok)
        except Exception:
            log.debug("could not sample the headband", exc_info=True)

    # ---------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        if self.session is None:
            return
        if self.player is not None and self.player.finished:
            self._stop()
            return

        state = self.session.state()
        self.beat_lbl.setText(f"{state.beat:.2f} Hz")
        half = state.beat / 2.0
        if self._mode() == "isochronic":
            self.tones_lbl.setText(f"{state.carrier:.0f} Hz pulsed")
        else:
            self.tones_lbl.setText(f"L {state.carrier - half:.1f}   R {state.carrier + half:.1f}")
        self.segment_lbl.setText(state.segment)
        band = state.band or "—"
        self.band_lbl.setText(band)
        self.band_lbl.setStyleSheet(
            f"color:{BAND_COLOR.get(band, '#e6e6ea')};font-size:16px;font-weight:600;"
        )

        if state.phase == PHASE_BASELINE:
            self.progress.setValue(int(state.baseline_progress * 100))
            self.beat_sub.setText("baseline — no tones yet")
            self.response_lbl.setText("––")
            self.status_lbl.setText(
                f"Measuring your resting rhythm… {int(state.baseline_progress * 100)}%"
            )
            return

        self.beat_sub.setText(
            f"beat frequency ({state.offset:+.2f} Hz tuned to you)"
            if abs(state.offset) > 0.01 else "beat frequency"
        )
        self.progress.setValue(int(100 * state.journey_t / max(1.0, self.session.journey.seconds)))
        self.playhead.setPos(state.journey_t / 60.0)

        if state.response_z is None:
            self.response_lbl.setText("––")
        else:
            colour = ("#5fd35f" if state.response_z >= 1.0
                      else "#e0a53a" if state.response_z >= 0.0 else "#e05a5a")
            self.response_lbl.setText(f"{state.response_z:+.2f} SD")
            self.response_lbl.setStyleSheet(f"color:{colour};font-size:16px;font-weight:600;")

        if state.adapting:
            self._trace.append((state.journey_t, state.beat))
            if len(self._trace) > 2:
                xs, ys = zip(*self._trace)
                self.actual_curve.setData(np.asarray(xs) / 60.0, np.asarray(ys))

        remaining = _mmss(state.remaining)
        if state.holding:
            self.status_lbl.setText(f"{remaining} left — signal poor, holding the frequency steady.")
        elif state.adapting:
            self.status_lbl.setText(f"{remaining} left — steering by your {state.band} response.")
        else:
            self.status_lbl.setText(f"{remaining} left.")

    # ---------------------------------------------------------------- export
    def _export(self) -> None:
        if self._worker is not None:
            return
        journey = self.journey
        offsets = self._learned_offsets()
        suffix = "-tuned" if offsets else ""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export journey to WAV",
            os.path.join(os.getcwd(), f"{journey.key}{suffix}.wav"), "WAV audio (*.wav)"
        )
        if not path:
            return

        dialog = QtWidgets.QProgressDialog(
            f"Rendering {journey.title}…", "Cancel", 0, 100, self)
        dialog.setWindowTitle("Export")
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setAutoClose(False)

        worker = _RenderWorker(journey, path, self.volume.value() / 100.0, self._mode(), offsets)
        self._worker = worker
        worker.progress.connect(dialog.setValue)
        dialog.canceled.connect(worker.cancel)

        def done(written: str) -> None:
            dialog.close()
            self._worker = None
            note = (" with your tuning baked in" if offsets else "")
            QtWidgets.QMessageBox.information(
                self, "Exported",
                f"Wrote {written}{note}.\n\nIt is an ordinary WAV — copy it to a phone "
                "or any player. Headphones still matter for binaural.")

        def failed(message: str) -> None:
            dialog.close()
            self._worker = None
            if message != "cancelled":
                QtWidgets.QMessageBox.warning(self, "Export failed", message)

        worker.finished_ok.connect(done)
        worker.failed.connect(failed)
        worker.start()

    # ---------------------------------------------------------------- teardown
    def shutdown(self) -> None:
        """Stop audio and any render in flight (called when the window closes)."""
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(2000)
            self._worker = None
        if self.session is not None:
            self._stop()
