# EEG Visualizer for Muse

A private, fully-local desktop app that visualizes and records EEG from the
**Muse S Athena** headband. Live raw EEG, per-band power
(δ Delta / θ Theta / α Alpha / β Beta / γ Gamma) in dB, signal quality, and CSV
recording with markers. Everything stays on your machine — no accounts, no
cloud. Runs on **Windows and macOS**.

The app lives in **[`local_mind_monitor/`](local_mind_monitor/)** — see its
[detailed README](local_mind_monitor/README.md) for everything below in depth.

## Highlights

- **Live band-power readout** — colour-coded δ/θ/α/β/γ cards showing each band's
  current absolute power in dB.
- **Scrolling charts** — raw EEG per electrode (TP9, AF7, AF8, TP10) plus a
  scrolling band-power (dB) graph with per-band symbols.
- **Signal quality** — per-electrode good/ok/bad indicators.
- **CSV recording + markers** — ~1 Hz spreadsheet-friendly rows.
- **Robust Bluetooth** — connects to the Athena over native BLE via
  [BrainFlow](https://brainflow.org), detects and auto-recovers a stalled
  stream, and logs everything locally.
- **The Chamber** — binaural / monaural / isochronic entrainment journeys that
  are steered by your own live band powers, and tell you afterwards whether
  anything actually moved.

## Quick start

```bash
git clone https://github.com/Diego759/EEG_visualizer_for_muse.git
cd EEG_visualizer_for_muse/local_mind_monitor
```

- **Windows** — double-click `Launch EEG Visualizer.bat`
- **macOS** — double-click `Launch EEG Visualizer.command`
  (first time: right-click → **Open**, and allow Bluetooth when macOS asks)

No headband yet? Use the **Test Mode** launcher to run on a synthetic signal and
confirm everything installs. The first launch builds a private `.venv` and
installs dependencies (~1 minute); every launch after that opens instantly.

## Requirements

- Python 3.11+ ([python.org](https://python.org/downloads/), or `brew install python` on macOS)
- A Muse S Athena headband (or Test Mode for a synthetic signal)
- Bluetooth Low Energy

## Sanity check

On real hardware, alpha power (8–13 Hz) rises when you close your eyes — a quick
way to confirm the signal is real.

## The Chamber

Binaural-beat apps are open loop: they play 10 Hz at you and assume something
happened. You are wearing an EEG headband, so the Chamber measures instead.

It records a quiet 60-second baseline, plays a journey through its frequency
arc, and then hunts — holding each candidate beat frequency long enough to score
the target band against *your* rest, and stepping toward whatever actually
raised it. At the end it prints your personal response curve, and every sample
lands in a `chamber_*.csv` next to your recordings.

Twelve journeys are built in (descents to theta, a 25-minute delta slide, a nap
with a real wake-up ramp, 40 Hz gamma, Schumann 7.83 Hz, a 45-minute focus
block…), all of them plain data you can copy and edit — nothing is locked and
there is nothing to buy. Binaural needs headphones; monaural and isochronic
modes work on speakers.

No Android build, and none needed: **Export WAV** writes an ordinary stereo file
— with your own tuning baked in — that plays on any phone, offline, forever.

```bash
python -m local_mind_monitor.chamber list
python -m local_mind_monitor.chamber play first-descent --adaptive
python -m local_mind_monitor.chamber render deep-delta -o deep-delta.wav
```

Not a medical device. Don't run a journey while driving; several are designed to
put you to sleep. See the
[detailed README](local_mind_monitor/README.md#the-chamber) for the full notes.

---

### Also in this repo

This repository also retains a small legacy **Globomantics CRM** training
project (`database.py`, `start.py`, `templates/`, `static/`, `data/`). Those
files are unrelated to the EEG app and are kept for history.
