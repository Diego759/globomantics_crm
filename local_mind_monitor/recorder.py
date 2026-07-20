"""CSV session recorder with a Mind Monitor-compatible column layout.

Writes ~1 Hz aggregated rows: per-channel band powers, latest raw EEG,
accelerometer/gyro, headband-on flag, per-electrode HSI, and a marker column
(``Elements``) that the GUI's marker button writes into.
"""

from __future__ import annotations

import csv
from datetime import datetime

from .processing import BANDS


class Recorder:
    def __init__(self, path: str, channel_names: list[str]):
        self.path = path
        self.channel_names = channel_names
        self._file = open(path, "w", newline="")
        self._writer = csv.writer(self._file)
        self._pending_marker: str = ""
        self._writer.writerow(self._header())
        self._file.flush()
        self.rows_written = 0

    def _header(self) -> list[str]:
        cols = ["TimeStamp"]
        for band in BANDS:
            cols += [f"{band}_{name}" for name in self.channel_names]
        cols += [f"RAW_{name}" for name in self.channel_names]
        cols += ["Accelerometer_X", "Accelerometer_Y", "Accelerometer_Z"]
        cols += ["Gyro_X", "Gyro_Y", "Gyro_Z"]
        cols += ["HeadBandOn"]
        cols += [f"HSI_{name}" for name in self.channel_names]
        cols += ["Elements"]
        return cols

    def add_marker(self, label: str) -> None:
        """Queue a marker; it lands in the ``Elements`` column of the next row."""
        self._pending_marker = label

    def write_row(
        self,
        band_powers: dict[int, list[float]],
        channels: list[int],
        raw_latest: list[float],
        accel: list[float],
        gyro: list[float],
        headband_on: bool,
        hsi: list[str],
    ) -> None:
        row: list = [datetime.now().isoformat(timespec="milliseconds")]
        for band_idx in range(len(BANDS)):
            for ch in channels:
                vals = band_powers.get(ch)
                row.append(round(vals[band_idx], 4) if vals is not None else "")
        row += [round(v, 4) for v in raw_latest]
        row += [round(v, 4) for v in (accel + [0, 0, 0])[:3]]
        row += [round(v, 4) for v in (gyro + [0, 0, 0])[:3]]
        row += [1 if headband_on else 0]
        row += hsi
        row += [self._pending_marker]
        self._pending_marker = ""
        self._writer.writerow(row)
        self._file.flush()
        self.rows_written += 1

    def close(self) -> None:
        if self._file and not self._file.closed:
            self._file.close()
