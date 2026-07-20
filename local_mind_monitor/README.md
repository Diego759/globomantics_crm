# Local Mind Monitor

A private, fully-local desktop replacement for [Mind Monitor](https://mind-monitor.com),
built for the **Muse S Athena**. Connects over native Bluetooth via
[BrainFlow](https://brainflow.org), shows live EEG and per-channel band powers,
and records Mind Monitor-style CSV sessions with markers. Nothing leaves your
machine — no accounts, no cloud.

## Features (v1)

- **Live band-power graphs** — raw EEG (TP9, AF7, AF8, TP10) plus per-channel
  Delta / Theta / Alpha / Beta / Gamma absolute band powers.
- **Signal quality** — per-electrode good/ok/bad indicators (Mind Monitor's HSI analog).
- **CSV recording + markers** — ~1 Hz rows in a Mind Monitor-compatible column
  layout, with a marker button that annotates the session.

## Why BrainFlow

The Muse S Athena uses a newer BLE protocol that older tools (e.g. `muse-lsl`)
don't fully support. BrainFlow ≥ 5.22.0 added a dedicated
`MUSE_S_ATHENA_BOARD` that speaks it over **native Bluetooth (no BLED112
dongle)** and ships `DataFilter.get_avg_band_powers()` for the band-power math.

## Windows: just double-click

Double-click **`Launch Local Mind Monitor.bat`** inside `local_mind_monitor\`
(copy it to your Desktop for easy access). The first double-click installs
everything into a private `.venv` folder and takes about a minute — every
double-click after that opens the app straight away, no terminal involved.
Requires Python 3.11+ from [python.org](https://python.org/downloads/) (check
"Add python.exe to PATH" during install) if you don't already have it.

## Install (manual / macOS / Linux)

```bash
pip install -r local_mind_monitor/requirements.txt
```

## Run

Without hardware (synthetic signal, for trying the UI and CSV output):

```bash
python -m local_mind_monitor.app --synthetic
```

With a real Muse S Athena (power it on, then):

```bash
python -m local_mind_monitor.app
# optionally target a specific device:
python -m local_mind_monitor.app --mac XX:XX:XX:XX:XX:XX
```

Click **Connect**, then **Start Recording** to save a `session_*.csv` in the
current directory. **Marker** annotates the next row. Sanity check on real
hardware: alpha power (8–13 Hz) rises when you close your eyes.

## Layout

| File | Role |
|---|---|
| `device.py` | BrainFlow session + background acquisition, ring buffers, 1 Hz recording |
| `processing.py` | Band powers + signal quality (no GUI dependency) |
| `recorder.py` | Mind Monitor-style CSV writer + markers |
| `gui/main_window.py` | Controls, signal-quality row, wiring |
| `gui/plots.py` | pyqtgraph raw-EEG and band-power widgets |
| `app.py` | Entry point / argument parsing |

## Not yet included (v2 ideas)

OSC/LSL re-broadcast, PPG heart-rate and fNIRS views, local session history,
and a packaged one-file executable.
