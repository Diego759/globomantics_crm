"""Muse S Athena acquisition via BrainFlow.

Pure Python (no GUI dependency): connects, buffers EEG/IMU into thread-safe
ring buffers on a background thread, and drives ~1 Hz recording. The GUI reads
snapshots from here on its own timer.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np
from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams, BrainFlowPresets

from . import processing
from .recorder import Recorder

log = logging.getLogger("local_mind_monitor.device")

# Athena default preset for BrainFlow; low_latency is Athena-only.
ATHENA_OTHER_INFO = "preset=p1041;low_latency=true"

# Seconds of EEG to keep in the ring buffer (drives plots and band powers).
BUFFER_SECONDS = 10
RECORD_INTERVAL = 1.0  # seconds between CSV rows

# Muse over native BLE can be slow to discover; give the scan a generous
# window and retry, since it often connects only on the 2nd or 3rd attempt.
BLE_DISCOVERY_TIMEOUT = 15  # seconds BrainFlow spends looking for the headband
CONNECT_ATTEMPTS = 3
CONNECT_RETRY_DELAY = 2.0  # seconds between attempts

# Muse BLE streams sometimes stall mid-session (phone app steals the link,
# interference, a brief drop) -- BrainFlow keeps the session "prepared" but
# stops delivering samples, so the plots freeze silently. Detect that and try
# a full reconnect, then give up and tell the user to reconnect by hand.
STALL_TIMEOUT = 3.0        # seconds with no new EEG before we call it stalled
RECOVERY_COOLDOWN = 5.0    # seconds before the first automatic reconnect attempt
MAX_RECOVERY_ATTEMPTS = 3  # after this many failures we say "lost" in the UI...
# ...but we KEEP RETRYING for the rest of the session.  Giving up used to be
# permanent, which quietly cost a whole recording: a link drop 12 minutes into a
# 40-minute tape got three attempts inside ~20s and then nothing at all for the
# remaining 28 minutes.  A meditation session is exactly when nobody is watching
# the screen, so the app has to keep trying on its own.  Back off between tries
# so we don't hammer the BLE stack (a full reconnect itself takes ~12-16s).
RECOVERY_BACKOFF_MAX = 60.0   # seconds; delay doubles 5 -> 10 -> 20 -> 40 -> 60

# Muse S Athena preset. BrainFlow defaults to p1041 ("5 EEG values"), but that
# preset only delivered an initial burst then stopped streaming on this setup.
# The reverse-engineered reference implementations (amused-py / OpenMuse) stream
# the Athena with p1034/p1035; p1035 = 4 EEG channels, matching the Athena's four
# electrodes (TP9/AF7/AF8/TP10). Override with --preset if needed.
ATHENA_PRESET = "p1035"
# low_latency sends BrainFlow's "L1" command. BrainFlow's own docs call
# low_latency=false the "more conservative device mode" and recommend it for
# long recordings; low_latency=true was stalling the stream a few seconds after
# connecting on this setup, so we default to the conservative mode.
ATHENA_LOW_LATENCY_DEFAULT = False

# Seconds of PPG/optical data to keep for the heart-rate estimate.
HR_BUFFER_SECONDS = 12

# BrainFlow reports the Athena battery as raw_uint16 * (1/512) -- see
# MUSE_ATHENA_BATTERY_PERCENT_SCALE_FACTOR in its muse_athena.cpp, a constant
# inherited from the older Muse telemetry format. On a fully charged Athena that
# reads 50%, i.e. raw 25600 -- which is exactly 100% in Q8.8 fixed point
# (100 * 256), so the Athena's true scale is 1/256. Doubling BrainFlow's number
# gives the real percentage. Override with --battery-scale if your firmware
# differs.
ATHENA_BATTERY_SCALE = 2.0


class DeviceNotFoundError(RuntimeError):
    """Raised when the headband could not be reached after retries."""


class MuseDevice:
    def __init__(
        self,
        synthetic: bool = False,
        mac_address: str | None = None,
        low_latency: bool = ATHENA_LOW_LATENCY_DEFAULT,
        preset: str = ATHENA_PRESET,
        battery_scale: float | None = None,
    ):
        BoardShim.disable_board_logger()
        self.synthetic = synthetic
        self._low_latency = low_latency
        self._preset = preset
        # The synthetic board already reports a true 0-100 percentage; only the
        # Athena needs BrainFlow's halved value corrected.
        if battery_scale is not None:
            self._battery_scale = float(battery_scale)
        else:
            self._battery_scale = 1.0 if synthetic else ATHENA_BATTERY_SCALE
        params = BrainFlowInputParams()
        if synthetic:
            self.board_id = BoardIds.SYNTHETIC_BOARD.value
        else:
            self.board_id = BoardIds.MUSE_S_ATHENA_BOARD.value
            ll = "true" if low_latency else "false"
            params.other_info = f"preset={preset};low_latency={ll}"
            params.timeout = BLE_DISCOVERY_TIMEOUT
            if mac_address:
                params.mac_address = mac_address
        self._params = params
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

        # Battery and PPG/optical (heart rate) live in a different preset than
        # EEG -- ancillary on the Athena, default on the synthetic board -- so
        # find where they are and which extra presets we must read each pull.
        self._battery = self._locate_channel("battery_channel")             # (preset, row) | None
        self._ppg = self._locate_channels(("optical_channels", "ppg_channels"))  # (preset, rows, sr) | None
        self._latest_battery: float | None = None
        if self._ppg is not None:
            _, ppg_rows, ppg_sr = self._ppg
            self._ppg_sr = ppg_sr
            self._ppg_buf = np.zeros((len(ppg_rows), max(1, int(ppg_sr * HR_BUFFER_SECONDS))))
        else:
            self._ppg_sr = 0
            self._ppg_buf = None
        self._ppg_filled = 0

        extra: set = set()
        if self._has_imu:
            extra.add(BrainFlowPresets.AUXILIARY_PRESET)
        for loc in (self._battery, self._ppg):
            if loc is not None:
                extra.add(loc[0])
        extra.discard(BrainFlowPresets.DEFAULT_PRESET)  # DEFAULT is always read
        self._extra_presets = sorted(extra, key=lambda p: p.value)

        self._running = False
        self._thread: threading.Thread | None = None
        self._recorder: Recorder | None = None
        self._last_record = 0.0
        self._record_paused = False    # recording paused because the stream stalled
        self._stale_skipped = 0        # rows not written during the current stall
        self._recorded_data_time = 0.0  # _last_data_time as of the last row written

        # Stall detection / recovery state.
        self._last_data_time = 0.0     # monotonic time new EEG last arrived
        self._stall_logged = False
        self._last_recover = 0.0
        self._recover_attempts = 0
        self._stream_lost = False      # gave up recovering; user must reconnect
        # Serialises BrainFlow board lifecycle calls (prepare/start/stop/release
        # and get_board_data) so the recovery thread and a Disconnect/close on
        # another thread can't touch the native session at the same time.
        self._board_lock = threading.Lock()
        # Serialises BrainFlow DataFilter calls: band powers run on the GUI
        # thread AND (while recording) on this one, and DataFilter isn't safe
        # to call from two threads at once.
        self._compute_lock = threading.Lock()

    def _preset_available(self, preset) -> bool:
        try:
            BoardShim.get_board_descr(self.board_id, preset)
            return True
        except Exception:
            return False

    _PRESETS = (
        BrainFlowPresets.DEFAULT_PRESET,
        BrainFlowPresets.AUXILIARY_PRESET,
        BrainFlowPresets.ANCILLARY_PRESET,
    )

    def _locate_channel(self, key: str):
        """Find (preset, row) for a scalar channel like ``battery_channel``."""
        for preset in self._PRESETS:
            try:
                d = BoardShim.get_board_descr(self.board_id, preset)
            except Exception:
                continue
            row = d.get(key)
            if isinstance(row, int):
                return (preset, row)
        return None

    def _locate_channels(self, keys: tuple[str, ...]):
        """Find (preset, rows, sampling_rate) for the first of ``keys`` present
        as a non-empty channel list (e.g. optical_channels / ppg_channels)."""
        for preset in self._PRESETS:
            try:
                d = BoardShim.get_board_descr(self.board_id, preset)
            except Exception:
                continue
            for key in keys:
                rows = d.get(key)
                if isinstance(rows, list) and rows:
                    return (preset, list(rows), int(d.get("sampling_rate", self.sampling_rate)))
        return None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.synthetic:
            log.info("Connecting (synthetic, %d Hz, %d ch)...", self.sampling_rate, len(self.eeg_channels))
        else:
            log.info(
                "Connecting (Muse S Athena, %d Hz, %d ch, preset=%s, low_latency=%s)...",
                self.sampling_rate, len(self.eeg_channels), self._preset,
                "true" if self._low_latency else "false",
            )
        self._prepare_with_retries()
        self._board.start_stream()
        # Grace period so a slow first-sample delivery isn't flagged as a stall.
        self._last_data_time = time.time()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("Streaming started")

    def _prepare_with_retries(self) -> None:
        """Try to open the session a few times. Muse BLE discovery is flaky
        and frequently succeeds only on a later attempt; the synthetic board
        connects instantly so it just runs once."""
        attempts = 1 if self.synthetic else CONNECT_ATTEMPTS
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._board.prepare_session()
                return
            except Exception as exc:  # noqa: BLE001 - surfaced below
                last_exc = exc
                # Release anything half-opened before the next try.
                try:
                    if self._board.is_prepared():
                        self._board.release_session()
                except Exception:
                    pass
                if attempt < attempts:
                    time.sleep(CONNECT_RETRY_DELAY)
        if self.synthetic:
            raise last_exc  # nothing user-actionable; show the raw error
        raise DeviceNotFoundError(
            "Couldn't find your Muse S Athena.\n\n"
            "Check that it's turned on, sitting close to this PC, and not "
            "connected to your phone or the Muse app. Then try Connect again.\n\n"
            f"(BrainFlow, {attempts} attempts: {last_exc})"
        )

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._recorder:
            self.stop_recording()
        self._release_board()

    def _release_board(self) -> None:
        """Tear down the BrainFlow session without letting a hung native call
        wedge shutdown. release_session() can block indefinitely if the BLE
        radio is in a bad state; the window would vanish but pythonw.exe would
        linger. Run it on a daemon thread and stop waiting after a few seconds
        -- a daemon thread never keeps the interpreter alive -- so closing the
        app always actually exits the process."""
        def _release() -> None:
            with self._board_lock:
                try:
                    if self._board.is_prepared():
                        self._board.stop_stream()
                        self._board.release_session()
                except Exception:
                    pass

        t = threading.Thread(target=_release, daemon=True)
        t.start()
        t.join(timeout=3.0)

    def _loop(self) -> None:
        while self._running:
            try:
                self._pull()
            except Exception:
                # Log (throttled) instead of swallowing silently -- a recurring
                # pull error is exactly the kind of thing that froze the plots
                # with no clue why.
                if not self._stall_logged:
                    log.exception("EEG pull failed")
            self._maybe_record()
            self._maybe_recover()
            time.sleep(0.05)

    def _maybe_recover(self) -> None:
        """Watch for a stalled stream and try to recover it. Muse over BLE can
        stop delivering samples while BrainFlow still reports the session as
        prepared; without this the GUI just freezes."""
        # NB: deliberately not bailing out on self._stream_lost -- that flag now
        # means "the user should know the signal is gone", not "stop trying".
        if self.synthetic or not self._running:
            return
        idle = time.time() - self._last_data_time
        if idle < STALL_TIMEOUT:
            if self._stall_logged:
                log.info("Stream recovered after %d reconnect attempt(s)", self._recover_attempts)
            self._stall_logged = False
            self._recover_attempts = 0
            self._stream_lost = False        # back in business
            return
        if not self._stall_logged:
            log.warning("No new EEG for %.1fs -- stream stalled", idle)
            self._stall_logged = True

        # Exponential backoff: 5s, 10s, 20s, 40s, then every 60s indefinitely.
        delay = min(RECOVERY_COOLDOWN * (2 ** max(0, self._recover_attempts - 1)),
                    RECOVERY_BACKOFF_MAX)
        now = time.time()
        if now - self._last_recover < delay:
            return
        self._last_recover = now
        self._recover_attempts += 1
        if self._recover_attempts > MAX_RECOVERY_ATTEMPTS and not self._stream_lost:
            self._stream_lost = True         # surface it in the UI, keep trying
            log.error("Signal still lost after %d attempts; continuing to retry every %.0fs",
                      MAX_RECOVERY_ATTEMPTS, RECOVERY_BACKOFF_MAX)
        log.info("Reconnecting to Muse (attempt %d, next retry in %.0fs if this fails)...",
                 self._recover_attempts,
                 min(RECOVERY_COOLDOWN * (2 ** self._recover_attempts), RECOVERY_BACKOFF_MAX))
        if self._reconnect():
            # Fresh grace window; if data flows again the next check clears the stall.
            self._last_data_time = time.time()
            log.info("Reconnect issued; waiting for data")

    def _reconnect(self) -> bool:
        """Full BLE reconnect: tear the session all the way down and rebuild it.
        A plain stop/start can't recover a dropped Muse link -- once the link is
        gone, start_stream fails with BOARD_WRITE_ERROR -- so we release and
        prepare the session again, which re-establishes the Bluetooth connection."""
        if not self._running:
            return False
        with self._board_lock:
            for name in ("stop_stream", "release_session"):
                try:
                    if self._board.is_prepared():
                        getattr(self._board, name)()
                except Exception as exc:
                    log.debug("%s during reconnect ignored: %s", name, exc)
            # Rebuild the session object so no stale native state carries over.
            try:
                self._board = BoardShim(self.board_id, self._params)
                self._board.prepare_session()
                self._board.start_stream()
                return True
            except Exception:
                log.exception("reconnect prepare/start failed")
                return False

    # ------------------------------------------------------------------ status
    def seconds_since_data(self) -> float:
        """How long since new EEG samples arrived (0 for the synthetic board)."""
        if self.synthetic or self._last_data_time == 0.0:
            return 0.0
        return time.time() - self._last_data_time

    def is_stalled(self) -> bool:
        return not self.synthetic and self.seconds_since_data() > STALL_TIMEOUT

    def stream_lost(self) -> bool:
        """True when the signal has been gone long enough that the user should
        know.  Automatic reconnection keeps running regardless."""
        return self._stream_lost

    def recovery_attempts(self) -> int:
        """Reconnect attempts made since the stream last delivered data."""
        return self._recover_attempts

    def _pull(self) -> None:
        # DEFAULT preset carries EEG (and, on the synthetic board, battery/PPG).
        with self._board_lock:
            data = self._board.get_board_data(preset=BrainFlowPresets.DEFAULT_PRESET)
        if data.shape[1] > 0:
            self._last_data_time = time.time()  # feeds stall detection
            self._ingest_eeg(data)
            self._ingest_battery_ppg(data, BrainFlowPresets.DEFAULT_PRESET)

        # Extra presets: IMU (auxiliary) and, on the Athena, battery + optical
        # PPG (ancillary). Each preset's buffer is drained once per pull.
        for preset in self._extra_presets:
            with self._board_lock:
                extra = self._board.get_board_data(preset=preset)
            if extra.shape[1] == 0:
                continue
            if preset == BrainFlowPresets.AUXILIARY_PRESET and self._has_imu:
                self._ingest_imu(extra)
            self._ingest_battery_ppg(extra, preset)

    def _ingest_eeg(self, data: np.ndarray) -> None:
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

    def _ingest_imu(self, aux: np.ndarray) -> None:
        ac, gy = self._imu_channels()
        if ac and aux.shape[0] > max(ac):
            self._latest_accel = [float(aux[c, -1]) for c in ac]
        if gy and aux.shape[0] > max(gy):
            self._latest_gyro = [float(aux[c, -1]) for c in gy]

    def _ingest_battery_ppg(self, data: np.ndarray, preset) -> None:
        if self._battery is not None and self._battery[0] == preset:
            row = self._battery[1]
            if data.shape[0] > row:
                self._update_battery(data[row, :])
        if self._ppg is not None and self._ppg[0] == preset and self._ppg_buf is not None:
            rows = [r for r in self._ppg[1] if r < data.shape[0]]
            if len(rows) == self._ppg_buf.shape[0]:
                self._append_ppg(data[rows, :])

    def _update_battery(self, samples: np.ndarray) -> None:
        """Hold the most recent *known* battery reading.

        The Athena sends battery in asynchronous status packets, so the channel
        reads 0 until the first one lands and then repeats the last value. Taking
        the newest non-zero sample means we show "--" while waiting instead of a
        misleading 0%. The raw value is logged whenever the displayed percentage
        changes, so the scale factor can be verified against the headband.
        """
        valid = samples[np.isfinite(samples) & (samples > 0)]
        if valid.size == 0:
            return
        raw = float(valid[-1])
        before = self.battery_level()
        self._latest_battery = raw
        after = self.battery_level()
        if after is not None and (before is None or round(before) != round(after)):
            log.info("Battery: raw=%.4f x%.2f -> %.0f%%", raw, self._battery_scale, after)

    def _append_ppg(self, chunk: np.ndarray) -> None:
        with self._lock:
            n = chunk.shape[1]
            if n >= self._ppg_buf.shape[1]:
                self._ppg_buf = chunk[:, -self._ppg_buf.shape[1] :].copy()
                self._ppg_filled = self._ppg_buf.shape[1]
            else:
                self._ppg_buf = np.roll(self._ppg_buf, -n, axis=1)
                self._ppg_buf[:, -n:] = chunk
                self._ppg_filled = min(self._ppg_filled + n, self._ppg_buf.shape[1])

    def _imu_channels(self) -> tuple[list[int], list[int]]:
        """Accel/gyro row indices for the AUX preset, looked up once and cached
        (the old code queried the board description on every pull)."""
        cached = getattr(self, "_imu_ch_cache", None)
        if cached is None:
            try:
                descr = BoardShim.get_board_descr(self.board_id, BrainFlowPresets.AUXILIARY_PRESET)
                cached = (descr.get("accel_channels", []), descr.get("gyro_channels", []))
            except Exception:
                cached = ([], [])
            self._imu_ch_cache = cached
        return cached

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
        # channels->local-row array here. Serialise DataFilter use against the
        # recording thread.
        with self._compute_lock:
            return processing.compute_band_powers(eeg, list(range(len(self.eeg_channels))), self.sampling_rate)

    def band_power_db(self) -> np.ndarray:
        """Absolute band power (averaged across channels) in dB, for the live
        readout and scrolling chart. NaN array until enough data is buffered."""
        eeg = self.snapshot()
        with self._compute_lock:
            return processing.band_power_db(eeg, list(range(len(self.eeg_channels))), self.sampling_rate)

    def signal_quality(self) -> list[str]:
        eeg = self.snapshot(seconds=2.0)
        if eeg.shape[1] == 0:
            return ["bad"] * len(self.eeg_channels)
        return [processing.signal_quality(eeg[i]) for i in range(eeg.shape[0])]

    def headband_on(self) -> bool:
        return any(q != "bad" for q in self.signal_quality())

    def battery_level(self) -> float | None:
        """Battery charge as a percentage (0-100), or None if not known yet."""
        if self._battery is None or self._latest_battery is None:
            return None
        return max(0.0, min(100.0, float(self._latest_battery) * self._battery_scale))

    def heart_rate(self) -> float | None:
        """Estimated heart rate in BPM from PPG/optical data, or None if there's
        no clear pulse yet (e.g. headband not seated, or synthetic board)."""
        if self._ppg_buf is None:
            return None
        with self._lock:
            if self._ppg_filled == 0:
                return None
            buf = self._ppg_buf[:, -self._ppg_filled :].copy()
        return processing.heart_rate(buf, self._ppg_sr)

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

        # Never record a dead stream.  snapshot() reads a ring buffer that still
        # holds the last samples received, so if BLE has dropped we would write
        # byte-identical rows once a second for the rest of the session.  Those
        # rows look like real data (timestamps advance, HSI still says "good")
        # but are a frozen value — they draw as a flat line and skew every
        # metric.  Better to leave a gap that is honestly missing.
        # Exact test: has the acquisition thread received anything since the row
        # we last wrote?  Using is_stalled() alone would still let through the
        # STALL_TIMEOUT-sized window of duplicates right after a drop.
        if self._last_data_time <= self._recorded_data_time or self.is_stalled():
            if not self._record_paused:
                log.warning("recording paused: no EEG for %.1fs - not writing stale rows",
                            self.seconds_since_data())
                self._record_paused = True
            self._stale_skipped += 1
            return
        if self._record_paused:
            log.info("recording resumed after a stall (%d row(s) skipped)", self._stale_skipped)
            self._record_paused = False
            self._stale_skipped = 0
        self._recorded_data_time = self._last_data_time

        eeg = self.snapshot()
        if eeg.shape[1] == 0:
            return
        local_channels = list(range(len(self.eeg_channels)))
        with self._compute_lock:
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
            battery=self.battery_level(),
            heart_rate=self.heart_rate(),
        )
