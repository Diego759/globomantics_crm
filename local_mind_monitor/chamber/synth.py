"""Real-time binaural / monaural / isochronic tone synthesis.

Pure numpy, no audio device and no GUI: :meth:`BinauralSynth.render` turns the
current parameters into the next block of stereo samples, so the same engine
drives live playback (``player.py``), offline WAV export (``render.py``) and the
tests -- which is what makes the DSP verifiable at all.

Why the parameters are slewed rather than set: a binaural session changes
frequency continuously (a journey glides alpha -> theta over minutes), and an
abrupt jump in frequency, gain or phase is an audible click straight into
someone's headphones while they are trying to drop into a trance. So every
parameter moves at a bounded rate and the oscillator phase is integrated across
block boundaries -- render() can be called with any block size, in any order of
parameter changes, and the output stays continuous.
"""

from __future__ import annotations

import numpy as np

# Modes.  Binaural is the classic effect (a beat that only exists in the
# listener's head, and only over headphones); the other two survive speakers.
MODE_BINAURAL = "binaural"
MODE_MONAURAL = "monaural"      # both tones in both ears -> real acoustic beat
MODE_ISOCHRONIC = "isochronic"  # one tone, pulsed on/off at the beat rate
MODES = (MODE_BINAURAL, MODE_MONAURAL, MODE_ISOCHRONIC)

AMBIENCE_KINDS = ("none", "pink", "brown")

# Slew limits (units per second).  Fast enough that the UI feels responsive,
# slow enough that nothing steps.
CARRIER_SLEW = 80.0   # Hz/s
BEAT_SLEW = 2.0       # Hz/s -- journeys glide far slower than this
GAIN_SLEW = 2.0       # full scale per second (i.e. 0 -> 1 in 0.5 s)

# Headroom.  Tones sit at -12 dBFS at gain 1.0 so that ambience, the isochronic
# pulse and the two summed tones of monaural mode can never add up past 0 dBFS.
TONE_PEAK = 0.25
AMBIENCE_PEAK = 0.18

# Seconds of seamless noise held for the ambience bed (see _make_noise_loop).
NOISE_LOOP_SECONDS = 20.0


class _Slew:
    """A scalar that walks toward its target at a bounded rate."""

    def __init__(self, value: float, rate: float):
        self.value = float(value)
        self.target = float(value)
        self.rate = float(rate)

    def block(self, frames: int, sample_rate: int) -> np.ndarray:
        """Return ``frames`` values ramping toward the target, and advance."""
        if frames <= 0:
            return np.zeros(0)
        step = self.rate / sample_rate
        delta = self.target - self.value
        needed = abs(delta) / step if step > 0 else np.inf
        if needed <= frames:
            # Reaches the target inside this block: ramp, then hold.
            n = max(1, int(np.ceil(needed)))
            ramp = np.linspace(self.value, self.target, n, endpoint=True)
            out = np.concatenate([ramp, np.full(frames - n, self.target)])[:frames]
            self.value = self.target
        else:
            end = self.value + np.sign(delta) * step * frames
            out = np.linspace(self.value, end, frames, endpoint=False)
            self.value = float(end)
        return out


def _make_noise_loop(kind: str, sample_rate: int, seconds: float, rng: np.random.Generator) -> np.ndarray:
    """Build one period of spectrally shaped noise that loops without a seam.

    Synthesising in the frequency domain and inverse-transforming gives noise
    that is *periodic by construction*, so the ambience bed can be read round
    and round a ring buffer forever with no click at the wrap -- much cheaper
    than filtering white noise per block, and with no filter state to keep.
    """
    n = int(sample_rate * seconds)
    n += n % 2
    freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
    # Skip DC; shape the rest.  pink: power 1/f (amplitude 1/sqrt(f));
    # brown: power 1/f^2 (amplitude 1/f) -- darker, more like distant surf.
    exponent = 0.5 if kind == "pink" else 1.0
    scale = np.zeros_like(freqs)
    scale[1:] = 1.0 / np.power(freqs[1:], exponent)
    # Roll off below 20 Hz: inaudible, and it would eat all the headroom.
    scale[freqs < 20.0] = 0.0
    spectrum = scale * (rng.normal(size=freqs.size) + 1j * rng.normal(size=freqs.size))
    noise = np.fft.irfft(spectrum, n)
    peak = float(np.max(np.abs(noise)))
    return (noise / peak if peak > 0 else noise).astype(np.float64)


class BinauralSynth:
    """Stateful block-based generator for one listening session.

    Set parameters whenever you like (``set(beat=6.0)``), then pull audio with
    ``render(frames)``.  Everything is clamped and slewed internally, so a UI
    slider, a journey schedule and the adaptive controller can all write to the
    same synth without coordinating.
    """

    def __init__(
        self,
        sample_rate: int = 44100,
        mode: str = MODE_BINAURAL,
        carrier: float = 200.0,
        beat: float = 10.0,
        gain: float = 0.0,
        ambience: float = 0.0,
        ambience_kind: str = "pink",
        seed: int | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
        if ambience_kind not in AMBIENCE_KINDS:
            raise ValueError(f"unknown ambience {ambience_kind!r}; expected one of {AMBIENCE_KINDS}")
        self.sample_rate = int(sample_rate)
        self.mode = mode
        self.ambience_kind = ambience_kind

        self._carrier = _Slew(carrier, CARRIER_SLEW)
        self._beat = _Slew(beat, BEAT_SLEW)
        self._gain = _Slew(gain, GAIN_SLEW)
        self._ambience = _Slew(ambience, GAIN_SLEW)

        # Phase accumulators (radians), carried across render() calls.
        self._phase_l = 0.0
        self._phase_r = 0.0
        self._phase_pulse = 0.0

        rng = np.random.default_rng(seed)
        self._noise: dict[str, np.ndarray] = {}
        for kind in ("pink", "brown"):
            self._noise[kind] = _make_noise_loop(kind, self.sample_rate, NOISE_LOOP_SECONDS, rng)
        # Independent read positions per ear so the bed is stereo-decorrelated
        # (identical noise in both ears collapses to a point in the middle of
        # your head, which fights the spaciousness the bed is there to create).
        self._noise_pos = [0, len(self._noise["pink"]) // 3]

    # ------------------------------------------------------------------ params
    @property
    def carrier(self) -> float:
        return self._carrier.target

    @property
    def beat(self) -> float:
        return self._beat.target

    @property
    def gain(self) -> float:
        return self._gain.target

    @property
    def ambience(self) -> float:
        return self._ambience.target

    def set(
        self,
        carrier: float | None = None,
        beat: float | None = None,
        gain: float | None = None,
        ambience: float | None = None,
        mode: str | None = None,
        ambience_kind: str | None = None,
    ) -> None:
        """Aim the synth at new parameters. Values are clamped, never rejected."""
        if carrier is not None:
            # Below ~40 Hz there is no usable carrier, and above ~1 kHz the
            # binaural effect falls apart (the auditory system switches from
            # phase to envelope cues), so clamp to the range where it works.
            self._carrier.target = float(np.clip(carrier, 40.0, 1000.0))
        if beat is not None:
            self._beat.target = float(np.clip(beat, 0.1, 50.0))
        if gain is not None:
            self._gain.target = float(np.clip(gain, 0.0, 1.0))
        if ambience is not None:
            self._ambience.target = float(np.clip(ambience, 0.0, 1.0))
        if mode is not None:
            if mode not in MODES:
                raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
            self.mode = mode
        if ambience_kind is not None:
            if ambience_kind not in AMBIENCE_KINDS:
                raise ValueError(f"unknown ambience {ambience_kind!r}")
            self.ambience_kind = ambience_kind

    def snap(self) -> None:
        """Jump every slewed parameter to its target immediately.

        Only for offline rendering, where there is no listener to click at and
        the first block should already be at the intended settings.
        """
        for s in (self._carrier, self._beat, self._gain, self._ambience):
            s.value = s.target

    def is_silent(self) -> bool:
        """True when nothing is sounding and nothing is on its way up."""
        return max(self._gain.value, self._gain.target,
                   self._ambience.value, self._ambience.target) <= 1e-6

    # ------------------------------------------------------------------ render
    def render(self, frames: int) -> np.ndarray:
        """Return the next ``frames`` stereo samples as float32 ``(frames, 2)``."""
        if frames <= 0:
            return np.zeros((0, 2), dtype=np.float32)
        sr = self.sample_rate
        carrier = self._carrier.block(frames, sr)
        beat = self._beat.block(frames, sr)
        gain = self._gain.block(frames, sr)
        ambience = self._ambience.block(frames, sr)

        if self.mode == MODE_ISOCHRONIC:
            left, right = self._render_isochronic(carrier, beat, sr)
        else:
            left, right = self._render_beats(carrier, beat, sr)

        tone = TONE_PEAK * gain
        left *= tone
        right *= tone

        if self.ambience_kind != "none":
            bed = self._render_ambience(frames)
            level = AMBIENCE_PEAK * ambience
            left += bed[0] * level
            right += bed[1] * level

        out = np.stack([left, right], axis=1)
        # Last line of defence.  Nothing above should be able to clip, but this
        # is going into headphones on someone's head: never emit out of range.
        np.clip(out, -1.0, 1.0, out=out)
        return out.astype(np.float32)

    def _render_beats(
        self, carrier: np.ndarray, beat: np.ndarray, sr: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """Binaural or monaural: two tones split symmetrically about the carrier.

        The perceived beat is the difference of the two, so putting them at
        ``carrier -/+ beat/2`` keeps the pair centred on the carrier no matter
        how the beat frequency moves.
        """
        f_low = np.maximum(carrier - beat / 2.0, 1.0)
        f_high = carrier + beat / 2.0
        # Phase is the running integral of frequency; integrating per sample is
        # what keeps the waveform continuous while the frequency glides.
        step = 2.0 * np.pi / sr
        phase_low = self._phase_l + step * np.cumsum(f_low)
        phase_high = self._phase_r + step * np.cumsum(f_high)
        self._phase_l = float(phase_low[-1] % (2.0 * np.pi))
        self._phase_r = float(phase_high[-1] % (2.0 * np.pi))

        tone_low = np.sin(phase_low)
        tone_high = np.sin(phase_high)
        if self.mode == MODE_MONAURAL:
            # Both tones to both ears: they interfere in the air/ear rather than
            # in the brainstem, so it works on speakers -- at half amplitude
            # each, since two summed sines peak at twice one.
            mixed = 0.5 * (tone_low + tone_high)
            return mixed, mixed.copy()
        return tone_low, tone_high

    def _render_isochronic(
        self, carrier: np.ndarray, beat: np.ndarray, sr: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """One tone, gated on and off at the beat rate with a raised-cosine
        envelope. No headphones needed, and no dependence on hearing both ears
        equally -- the reason this mode exists alongside binaural."""
        step = 2.0 * np.pi / sr
        phase = self._phase_l + step * np.cumsum(carrier)
        self._phase_l = float(phase[-1] % (2.0 * np.pi))
        self._phase_r = self._phase_l

        pulse_phase = self._phase_pulse + step * np.cumsum(beat)
        self._phase_pulse = float(pulse_phase[-1] % (2.0 * np.pi))
        # 0..1 envelope; raised cosine rather than a square gate, because a hard
        # gate splatters broadband clicks at every pulse edge.
        envelope = 0.5 * (1.0 - np.cos(pulse_phase))
        tone = np.sin(phase) * envelope
        return tone, tone.copy()

    def _render_ambience(self, frames: int) -> tuple[np.ndarray, np.ndarray]:
        loop = self._noise[self.ambience_kind]
        out = []
        for ear in (0, 1):
            pos = self._noise_pos[ear]
            idx = (np.arange(frames) + pos) % loop.size
            out.append(loop[idx])
            self._noise_pos[ear] = int((pos + frames) % loop.size)
        return out[0], out[1]
