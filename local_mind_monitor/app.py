"""Entry point: python -m local_mind_monitor.app [--synthetic] [--mac ADDR]."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Private, local Mind Monitor for Muse S Athena")
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
    args = parser.parse_args()

    # Imported here so the GUI deps are only required when actually launching.
    from PySide6 import QtWidgets

    from .gui.main_window import MainWindow

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(synthetic=args.synthetic, mac_address=args.mac)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
