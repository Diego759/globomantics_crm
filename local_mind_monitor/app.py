"""Entry point: python -m local_mind_monitor.app [--synthetic] [--mac ADDR]."""

from __future__ import annotations

import argparse
import logging
import os
import sys


def _setup_logging() -> str:
    """Log to a file next to the app. Under pythonw there is no console, so a
    file is the only way to see connect/stall/crash detail after the fact.
    Returns the log path."""
    log_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(log_dir, "local_mind_monitor.log")
    root = logging.getLogger("local_mind_monitor")
    if not root.handlers:
        root.setLevel(logging.INFO)
        try:
            from logging.handlers import RotatingFileHandler

            handler = RotatingFileHandler(
                log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
            root.addHandler(handler)
        except Exception:
            pass  # logging must never stop the app from starting

    # Route otherwise-silent uncaught exceptions to the log too.
    def _hook(exc_type, exc, tb):
        root.error("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _hook
    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Private, local EEG visualizer for the Muse S Athena")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Use BrainFlow's synthetic board instead of a real Muse (for testing without hardware)",
    )
    parser.add_argument(
        "--mac",
        default=None,
        help="Optional MAC address to target a specific Muse S Athena",
    )
    parser.add_argument(
        "--low-latency",
        action="store_true",
        help="Ask the Muse for BrainFlow's low-latency (L1) mode. Off by default: "
        "the conservative mode is more stable and low-latency was stalling the stream.",
    )
    parser.add_argument(
        "--preset",
        default="p1035",
        help="Muse S Athena BrainFlow preset (e.g. p1035, p1034, p1041). Default p1035 "
        "(4 EEG channels); p1041 only delivered an initial burst then stalled here.",
    )
    args = parser.parse_args()

    log_path = _setup_logging()
    logging.getLogger("local_mind_monitor").info(
        "Starting (%s). Log: %s", "synthetic" if args.synthetic else "muse", log_path
    )

    # Imported here so the GUI deps are only required when actually launching.
    from PySide6 import QtCore, QtWidgets

    from .gui.main_window import MainWindow
    from .single_instance import SingleInstance

    app = QtWidgets.QApplication(sys.argv)

    # If a copy is already open, ask it to come to the front and bow out
    # instead of stacking a second (possibly invisible) process.
    guard = SingleInstance()
    if guard.already_running():
        return 0

    window = MainWindow(
        synthetic=args.synthetic,
        mac_address=args.mac,
        low_latency=args.low_latency,
        preset=args.preset,
    )

    def _surface() -> None:
        window.setWindowState(
            (window.windowState() & ~QtCore.Qt.WindowMinimized) | QtCore.Qt.WindowActive
        )
        window.show()
        window.raise_()
        window.activateWindow()

    guard.activated.connect(_surface)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
