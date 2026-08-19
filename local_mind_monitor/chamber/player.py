"""Live audio output for a ChamberSession, via sounddevice/PortAudio.

Isolated in its own module -- and imported lazily -- so that every other part of
the Chamber (journeys, synthesis, WAV export, the closed loop) keeps working on
a machine with no audio stack at all.
"""

from __future__ import annotations

import logging

from .session import ChamberSession

log = logging.getLogger("local_mind_monitor.chamber.player")

# 1024 frames @ 44.1 kHz is ~23 ms: short enough that stop/skip feels instant,
# long enough that the numpy work per callback is trivial.
BLOCKSIZE = 1024


class AudioUnavailable(RuntimeError):
    """Raised when there is no usable audio output."""


def _sounddevice():
    try:
        import sounddevice as sd
    except Exception as exc:  # ImportError, or PortAudio missing at OS level
        raise AudioUnavailable(
            "Audio output needs the 'sounddevice' package (pip install sounddevice). "
            f"You can still export a journey to WAV without it. [{exc}]"
        ) from exc
    return sd


class Player:
    """Pulls blocks from a session and pushes them to the sound card."""

    def __init__(self, session: ChamberSession, device=None, blocksize: int = BLOCKSIZE):
        self.session = session
        self.device = device
        self.blocksize = blocksize
        self._stream = None
        self._finished = False

    @property
    def finished(self) -> bool:
        return self._finished or self.session.finished

    def start(self) -> None:
        sd = _sounddevice()

        def callback(outdata, frames, time_info, status):
            if status:
                log.debug("audio status: %s", status)
            block = self.session.render(frames)
            outdata[:] = block
            if self.session.finished:
                self._finished = True
                raise sd.CallbackStop()

        try:
            self._stream = sd.OutputStream(
                samplerate=self.session.sample_rate,
                channels=2,
                dtype="float32",
                blocksize=self.blocksize,
                device=self.device,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            raise AudioUnavailable(f"Could not open an audio output stream: {exc}") from exc

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.debug("error closing audio stream", exc_info=True)
            self._stream = None
        self._finished = True


def list_devices() -> str:
    """Text listing of audio outputs, for troubleshooting from the CLI."""
    try:
        sd = _sounddevice()
    except AudioUnavailable as exc:
        return str(exc)
    return str(sd.query_devices())
