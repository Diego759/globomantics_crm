"""Single-instance guard.

Double-clicking a launcher again should surface the window that is already
open, not spawn a second (often invisible) ``pythonw.exe``. This uses a local
socket as both the lock and an activation channel: the first instance listens;
a second instance connects, asks the first to come to the front, and exits.

It is written to fail *open* -- if anything about the guard misbehaves it lets
the app start normally, so the guard can never be the reason a launch does
nothing.
"""

from __future__ import annotations

from PySide6 import QtCore, QtNetwork

# Bump the suffix if the handshake protocol ever changes.
_KEY = "local_mind_monitor_single_instance_v1"


class SingleInstance(QtCore.QObject):
    """Emits :attr:`activated` when another launch asks us to surface."""

    activated = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._server: QtNetwork.QLocalServer | None = None

    def already_running(self) -> bool:
        """Return True if another instance is live (it has been asked to come
        to the front). Return False if we are the first instance -- in which
        case we now own the lock and will emit :attr:`activated` on later
        launches."""
        try:
            probe = QtNetwork.QLocalSocket()
            probe.connectToServer(_KEY)
            if probe.waitForConnected(300):
                probe.write(b"activate")
                probe.waitForBytesWritten(500)
                probe.disconnectFromServer()
                return True

            # Nobody answered. A leftover socket from a crashed instance would
            # block listen(), so clear it, then claim the name ourselves.
            QtNetwork.QLocalServer.removeServer(_KEY)
            self._server = QtNetwork.QLocalServer(self)
            self._server.newConnection.connect(self._on_new_connection)
            if not self._server.listen(_KEY):
                self._server = None  # couldn't listen; don't block startup
            return False
        except Exception:
            return False  # fail open -- never block a launch

    def _on_new_connection(self) -> None:
        if self._server is not None:
            conn = self._server.nextPendingConnection()
            if conn is not None:
                conn.disconnectFromServer()
        self.activated.emit()
