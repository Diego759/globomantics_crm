"""Command line for the Chamber: list, preview, render and play journeys.

    python -m local_mind_monitor.chamber list
    python -m local_mind_monitor.chamber show first-descent
    python -m local_mind_monitor.chamber render deep-delta -o deep-delta.wav
    python -m local_mind_monitor.chamber play theta-gateway --adaptive

Everything except ``play`` runs with no audio hardware and no headband, which is
also how the tests drive it.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import journeys as J
from .session import ChamberSession, format_report

# How often the headband is sampled for the closed loop. Band powers come off a
# 2 s window, so faster than this buys nothing but CPU.
EEG_INTERVAL = 0.5


def _fmt_minutes(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def cmd_list(args) -> int:
    rows = list(J.BUILTIN)
    if args.journey_dir:
        rows += J.user_journeys(args.journey_dir)
    print(f"{'KEY':<18}{'LENGTH':>8}  {'MODE':<11}{'TARGETS':<22}TITLE")
    for j in rows:
        print(f"{j.key:<18}{_fmt_minutes(j.seconds):>8}  {j.mode:<11}"
              f"{','.join(j.bands) or '-':<22}{j.title}")
    print(f"\n{len(rows)} journeys. All included, nothing locked.")
    return 0


def cmd_show(args) -> int:
    j = J.get(args.journey)
    print(f"{j.title}  [{j.key}]")
    print(f"{j.summary}")
    print(f"{_fmt_minutes(j.seconds)} · {j.mode} · {j.ambience_kind} ambience · "
          f"targets {', '.join(j.bands) or '-'}")
    if j.caution:
        print(f"!  {j.caution}")
    print()
    print(f"{'SEGMENT':<14}{'LENGTH':>7}  {'BEAT':<16}{'CARRIER':<16}BAND")
    for seg in j.segments:
        beat = (f"{seg.beat_start:g} Hz" if seg.end_beat == seg.beat_start
                else f"{seg.beat_start:g} → {seg.end_beat:g} Hz")
        carrier = (f"{seg.carrier_start:g} Hz" if seg.end_carrier == seg.carrier_start
                   else f"{seg.carrier_start:g} → {seg.end_carrier:g} Hz")
        print(f"{seg.name:<14}{_fmt_minutes(seg.seconds):>7}  {beat:<16}{carrier:<16}{seg.band}")
        if seg.note:
            print(f"    {seg.note}")
    return 0


def cmd_render(args) -> int:
    from .render import render_journey

    j = _journey_from_args(args)
    out = Path(args.output or f"{j.key}.wav")
    offsets = _parse_offsets(args.tune)
    print(f"Rendering {j.title} ({_fmt_minutes(j.seconds)}) → {out}")
    if offsets:
        print("  tuned:", ", ".join(f"{b} {o:+g} Hz" for b, o in offsets.items()))

    last = [-1]

    def progress(fraction: float) -> None:
        pct = int(fraction * 100)
        if pct != last[0] and pct % 5 == 0:
            last[0] = pct
            print(f"\r  {pct:3d}%", end="", flush=True)

    render_journey(
        j, out, sample_rate=args.sample_rate, volume=args.volume,
        mode=args.mode, beat_offsets=offsets, progress=progress,
    )
    size_mb = out.stat().st_size / 1e6
    print(f"\r  done — {size_mb:.1f} MB. Copy it to any phone; no app needed.")
    return 0


def cmd_devices(_args) -> int:
    from .player import list_devices

    print(list_devices())
    return 0


def cmd_play(args) -> int:
    from .player import AudioUnavailable, Player

    j = _journey_from_args(args)
    device = None
    if args.adaptive:
        device = _open_headband(args)
        if device is None:
            return 1

    log_path = Path(args.log) if args.log else None
    session = ChamberSession(
        j, sample_rate=args.sample_rate, volume=args.volume, mode=args.mode,
        adaptive=device is not None, baseline_seconds=args.baseline, log_path=log_path,
    )

    print(f"{j.title} — {_fmt_minutes(j.seconds)}"
          + (f" (+{int(args.baseline)}s baseline)" if device is not None else ""))
    if j.caution:
        print(f"!  {j.caution}")
    if session.synth.mode == "binaural":
        print("   Headphones required — the beat only exists when each ear gets its own tone.")
    print("   Ctrl-C to stop early.\n")

    stop_eeg = threading.Event()
    feeder = None
    if device is not None:
        feeder = threading.Thread(target=_feed_loop, args=(device, session, stop_eeg), daemon=True)
        feeder.start()

    player = Player(session)
    try:
        player.start()
    except AudioUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        stop_eeg.set()
        if device is not None:
            device.stop()
        return 1

    try:
        while not player.finished:
            _print_status(session)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        player.stop()
        stop_eeg.set()
        if feeder is not None:
            feeder.join(timeout=2.0)
        report = session.stop()
        if device is not None:
            device.stop()

    print("\n" + format_report(report))
    if log_path:
        print(f"\nPer-sample log: {log_path}")
    _offer_tuned_export(report, j, args)
    return 0


def _print_status(session: ChamberSession) -> None:
    s = session.state()
    if s.phase == "baseline":
        line = (f"  baseline {s.baseline_progress * 100:3.0f}%  "
                f"(measuring your resting rhythm — no tones yet)")
    else:
        z = "--" if s.response_z is None else f"{s.response_z:+.2f}"
        tune = f" ({s.offset:+.2f} Hz tuned)" if abs(s.offset) > 0.01 else ""
        hold = "  [signal poor — holding]" if s.holding else ""
        line = (f"  {_fmt_minutes(s.journey_t)}/{_fmt_minutes(session.journey.seconds)}  "
                f"{s.segment:<12} {s.beat:5.2f} Hz{tune} on {s.carrier:6.1f} Hz  "
                f"{s.band or '-':<6} response {z} SD{hold}")
    print(f"\r{line:<110}", end="", flush=True)


def _feed_loop(device, session: ChamberSession, stop: threading.Event) -> None:
    """Sample the headband and push relative band powers into the session."""
    while not stop.is_set():
        try:
            powers = device.band_powers()
            if powers:
                rel = np.mean(np.stack(list(powers.values())), axis=0)
                quality = device.signal_quality()
                ok = sum(q in ("good", "ok") for q in quality) >= max(1, len(quality) // 2)
                if float(np.sum(rel)) > 0:
                    session.feed_bands(rel / np.sum(rel), quality_ok=ok)
        except Exception:
            pass  # a bad sample must never take the audio down with it
        stop.wait(EEG_INTERVAL)


def _open_headband(args):
    try:
        from ..device import MuseDevice
    except Exception as exc:
        print(f"error: closed-loop mode needs BrainFlow installed ({exc})", file=sys.stderr)
        return None
    print("Connecting to the headband…")
    try:
        device = MuseDevice(synthetic=args.synthetic, mac_address=args.mac)
        device.start()
    except Exception as exc:
        print(f"error: could not connect ({exc}). Run without --adaptive to play open loop.",
              file=sys.stderr)
        return None
    print(f"Connected @ {device.sampling_rate} Hz")
    return device


def _offer_tuned_export(report: dict, journey: J.Journey, args) -> None:
    """After an adaptive session, print the command that bakes in what was learnt."""
    tuned = {band: round(r["final_offset"], 2)
             for band, r in report.get("bands", {}).items()
             if r.get("best_beat") is not None and abs(r["final_offset"]) > 0.01}
    if not tuned:
        return
    spec = ",".join(f"{b}:{o:+g}" for b, o in tuned.items())
    print("\nTo keep this tuning as a file you can play anywhere:")
    print(f"  python -m local_mind_monitor.chamber render {journey.key} "
          f"--tune '{spec}' -o {journey.key}-tuned.wav")


def _parse_offsets(spec: str | None) -> dict[str, float]:
    """Parse ``Alpha:-0.8,Theta:+0.4`` into {band: offset}."""
    if not spec:
        return {}
    out: dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise SystemExit(f"bad --tune entry {part!r}; expected Band:offset")
        band, value = part.split(":", 1)
        out[band.strip().title()] = float(value)
    return out


def _journey_from_args(args) -> J.Journey:
    if getattr(args, "beat", None):
        return J.custom(
            beat=args.beat, minutes=args.minutes or 20.0,
            carrier=args.carrier or 200.0, mode=args.mode or "binaural",
        )
    return J.get(args.journey)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m local_mind_monitor.chamber",
        description="Binaural / isochronic journeys, optionally steered by your own EEG.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list the built-in journeys")
    p_list.add_argument("--journey-dir", default=None, help="also list *.json journeys from here")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="print a journey's timeline")
    p_show.add_argument("journey")
    p_show.set_defaults(func=cmd_show)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("journey", nargs="?", default="first-descent")
    common.add_argument("--beat", type=float, default=None,
                        help="ignore the journey and hold this beat frequency (Hz)")
    common.add_argument("--minutes", type=float, default=None, help="length for --beat")
    common.add_argument("--carrier", type=float, default=None, help="carrier for --beat")
    common.add_argument("--mode", choices=("binaural", "monaural", "isochronic"), default=None)
    common.add_argument("--volume", type=float, default=0.85)
    common.add_argument("--sample-rate", type=int, default=44100)

    p_render = sub.add_parser("render", parents=[common], help="write a journey to a WAV file")
    p_render.add_argument("-o", "--output", default=None)
    p_render.add_argument("--tune", default=None,
                          help="per-band offsets from an adaptive session, e.g. 'Alpha:-0.8'")
    p_render.set_defaults(func=cmd_render)

    p_play = sub.add_parser("play", parents=[common], help="play a journey now")
    p_play.add_argument("--adaptive", action="store_true",
                        help="connect the Muse and steer the beat by measured band power")
    p_play.add_argument("--baseline", type=float, default=60.0,
                        help="seconds of quiet rest measured before the tones start")
    p_play.add_argument("--log", default=None, help="write a per-sample CSV here")
    p_play.add_argument("--mac", default=None, help="MAC address of the headband")
    p_play.add_argument("--synthetic", action="store_true",
                        help="use BrainFlow's synthetic board instead of a real Muse")
    p_play.set_defaults(func=cmd_play)

    p_dev = sub.add_parser("devices", help="list audio output devices")
    p_dev.set_defaults(func=cmd_devices)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
