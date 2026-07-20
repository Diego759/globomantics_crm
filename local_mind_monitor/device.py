"""Muse S Athena acquisition via BrainFlow.

Pure Python (no GUI dependency): connects, buffers EEG/IMU into thread-safe
ring buffers on a background thread, and drives ~1 Hz recording. The GUI reads
snapshots from here on its own timer.
"""

from __future__ import annotations

import threading
import time

import numpy as np
from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams, BrainFlowPresets

from . import processing
from .recorder import Recorder

# Athena default preset for BrainFlow; low_latency is Athena-only.
ATHENA_OTHER_INFO = "preset=p1041;low_latency=true"

# Seconds of EEG to keep in the ring buffer (drives plots and band powers).
BUFFER_SECONDS = 10
RECORD_INTERVAL = 1.0  # seconds between CSV rows


class MuseDevice:
    def __init__(self, synthetic: bool = False, mac_address: str | None = None):
        BoardShim.disable_board_logger()
        self.synthetic = synthetic
        params = BrainFlowInputParams()
        if synthetic:
            self.board_id = BoardIds.SYNTHETIC_BOARD.value
        else:
            self.board_id = BoardIds.MUSE_S_ATHENA_BOARD.value
            params.other_info = ATHENA_OTHER_INFO
            if mac_address:
                params.mac_address = mac_address
        self._board = BoardShim(self.board_id, params)

        self.sampling_rate = BoardShim.get_sampling_rate(self.board_id)
        all_eeg = BoardShim.get_eeg_channels(self.board_id)
        # Muse has 4 EEG electrodes; the synthetic board exposes more, so cap.
        self.eeg_channels = all_eeg[:4]
        default_names = ["TP9", "AF7", "AF8", "TP10"]
        self.channel_names = default_names[: len(self.eeg_channels)]

        self._has_imu = self._preset_available(BrainFlowPresets.AUXILIARY_PRESET)

        buf_len = int(self.sampling_rate * BUFFER_SECONDS)
        self._eeg_buf = np.zeros((len(self.eeg_channels), buf_len))
        self._filled = 0
        self._lock = threading.Lock()

        self._latest_accel = [0.0, 0.0, 0.0]
        self._latest_gyro = [0.0, 0.0, 0.0]

        self._running = False
        self._thread: threading.Thread | None = None
        self._recorder: Recorder | None = None
        self._last_record = 0.0

    def _preset_available(self, preset) -> bool:
        try:
            BoardShim.get_board_descr(self.board_id, preset)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._board.prepare_session()
        self._board.start_stream()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._recorder:
            self.stop_recording()
        try:
            if self._board.is_prepared():
                self._board.stop_stream()
                self._board.release_session()
        except Exception:
            pass

    def _loop(self) -> None:
        while self._running:
            try:
                self._pull()
            except Exception:
                pass
            self._maybe_record()
            time.sleep(0.05)

    def _pull(self) -> None:
        data = self._board.get_board_data(preset=BrainFlowPresets.DEFAULT_PRESET)
        if data.shape[1] > 0:
            chunk = data[self.eeg_channels, :]
            with self._lock:
                n = chunk.shape[1]
                if n >= self._eeg_buf.shape[1]:
                    self._eeg_buf = chunk[:, -self._eeg_buf.shape[1] :].copy()
                    self._filled = self._eeg_buf.shape[1]
                else:
                    self._eeg_buf = np.roll(self._eeg_buf, -n, axis=1)
                    self._eeg_buf[:, -n:] = chunk
                    self._filled = min(self._filled + n, self._eeg_buf.shape[1])

        if self._has_imu:
            aux = self._board.get_board_data(preset=BrainFlowPresets.AUXILIARY_PRESET)
            if aux.shape[1] > 0:
                descr = BoardShim.get_board_descr(self.board_id, BrainFlowPresets.AUXILIARY_PRESET)
                ac = descr.get("accel_channels", [])
                gy = descr.get("gyro_channels", [])
                if ac:
                    self._latest_accel = [float(aux[c, -1]) for c in ac]
                if gy:
                    self._latest_gyro = [float(aux[c, -1]) for c in gy]

    # ------------------------------------------------------------------ snapshots
    def snapshot(self, seconds: float | None = None) -> np.ndarray:
        """Return the most recent EEG window (channels x samples)."""
        with self._lock:
            if self._filled == 0:
                return np.zeros((len(self.eeg_channels), 0))
            buf = self._eeg_buf[:, -self._filled :].copy()
        if seconds is not None:
            n = int(self.sampling_rate * seconds)
            buf = buf[:, -n:]
        return buf

    def band_powers(self) -> dict[int, np.ndarray]:
        eeg = self.snapshot()
        # compute_band_powers indexes into full-board rows, so build a
        # channels->local-row array here.
        return processing.compute_band_powers(eeg, list(range(len(self.eeg_channels))), self.sampling_rate)

    def signal_quality(self) -> list[str]:
        eeg = self.snapshot(seconds=2.0)
        if eeg.shape[1] == 0:
            return ["bad"] * len(self.eeg_channels)
        return [processing.signal_quality(eeg[i]) for i in range(eeg.shape[0])]

    def headband_on(self) -> bool:
        return any(q != "bad" for q in self.signal_quality())

    # ------------------------------------------------------------------ recording
    def start_recording(self, path: str) -> None:
        self._recorder = Recorder(path, self.channel_names)
        self._last_record = 0.0

    def stop_recording(self) -> None:
        if self._recorder:
            self._recorder.close()
            self._recorder = None

    def add_marker(self, label: str) -> None:
        if self._recorder:
            self._recorder.add_marker(label)

    @property
    def is_recording(self) -> bool:
        return self._recorder is not None

    def _maybe_record(self) -> None:
        if not self._recorder:
            return
        now = time.time()
        if now - self._last_record < RECORD_INTERVAL:
            return
        self._last_record = now
        eeg = self.snapshot()
        if eeg.shape[1] == 0:
            return
        local_channels = list(range(len(self.eeg_channels)))
        bp = processing.compute_band_powers(eeg, local_channels, self.sampling_rate)
        bp = {ch: list(v) for ch, v in bp.items()}
        raw_latest = [float(eeg[i, -1]) for i in local_channels]
        hsi = [processing.signal_quality(eeg[i, -int(self.sampling_rate * 2):]) for i in local_channels]
        self._recorder.write_row(
            band_powers=bp,
            channels=local_channels,
            raw_latest=raw_latest,
            accel=list(self._latest_accel),
            gyro=list(self._latest_gyro),
            headband_on=any(q != "bad" for q in hsi),
            hsi=hsi,
        )
