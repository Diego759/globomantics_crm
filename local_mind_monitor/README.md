# Local Mind Monitor

A private, fully-local desktop replacement for [Mind Monitor](https://mind-monitor.com),
built for the **Muse S Athena**. Connects over native Bluetooth via
[BrainFlow](https://brainflow.org), shows live EEG and per-channel band powers,
and records Mind Monitor-style CSV sessions with markers. Nothing leaves your
machine — no accounts, no cloud. Runs on **Windows and macOS**.

## Get the code

```bash
git clone https://github.com/Diego759/EEG_visualizer_for_muse.git
```

The app lives in the **`local_mind_monitor/`** subfolder of the repo — that's
where the launchers below live.

## Features

- **Live band-power readout** — a colour-coded row of cards (δ Delta / θ Theta /
  α Alpha / β Beta / γ Gamma) showing each band's current absolute power in dB.
- **Scrolling charts** — raw EEG per electrode (TP9, AF7, AF8, TP10) and a
  Mind Monitor-style scrolling band-power (dB) graph with the Greek symbol
  riding the end of each line.
- **Signal quality** — per-electrode good/ok/bad indicators (Mind Monitor's HSI analog).
- **CSV recording + markers** — ~1 Hz rows in a Mind Monitor-compatible column
  layout, with a marker button that annotates the session.
- **Robust connection** — detects a stalled Bluetooth stream, auto-reconnects,
  and tells you in the status bar instead of freezing silently. All activity is
  logged to `local_mind_monitor.log` next to the app.

## Windows: just double-click

Double-click **`Launch Local Mind Monitor.bat`** inside `local_mind_monitor\`.
The first double-click installs everything into a private `.venv` folder and
takes about a minute — every double-click after that opens the app straight
away. Requires Python 3.11+ from [python.org](https://python.org/downloads/)
(check "Add python.exe to PATH" during install) if you don't already have it.

## macOS: just double-click

Double-click **`Launch Local Mind Monitor.command`** inside `local_mind_monitor/`.
Same idea: the first run builds a private `.venv` and installs dependencies,
later runs launch instantly. Requires Python 3.11+ from
[python.org](https://python.org/downloads/) (or `brew install python`).

The first time, macOS Gatekeeper may block a downloaded `.command`. If
double-click does nothing, **right-click → Open** once (or run
`chmod +x "Launch Local Mind Monitor.command"` in Terminal). macOS will also ask
for **Bluetooth permission** the first time the app scans for the headband —
allow it.

## Try it without a headband

To confirm it installs and runs before your Muse is on hand, use the test-mode
launcher — **`Launch Local Mind Monitor (Test Mode).bat`** (Windows) or
**`Launch Local Mind Monitor (Test Mode).command`** (macOS). It runs on a
synthetic signal (the window title shows "(synthetic)") and shares the same
`.venv`, so it's instant once either launcher has been run once.

## Can't connect to the Muse?

`Connect` retries a few times, and if the stream stalls it auto-reconnects. If
it still can't reach the headband, run the Bluetooth check —
**`Bluetooth Check.bat`** (Windows) or **`Bluetooth Check.command`** (macOS). It
scans for nearby Bluetooth LE devices and tells you whether this computer can
see the headband at all, and prints its address.

- **Muse listed** → Bluetooth works and the headband is discoverable; retry
  `Connect` (and make sure your phone's Muse app isn't holding the link).
- **Muse not listed** → it's off, asleep, too far, still connected to your
  phone, or this computer's adapter doesn't support Bluetooth Low Energy.

If a launch ever seems stuck, **`Close Local Mind Monitor`** (`.bat` / `.command`)
force-closes any running instance.

## Install / run manually (any OS)

```bash
pip install -r local_mind_monitor/requirements.txt

# synthetic signal, for trying the UI and CSV output:
python -m local_mind_monitor.app --synthetic

# real Muse S Athena (power it on first):
python -m local_mind_monitor.app
```

Options:

```bash
python -m local_mind_monitor.app --mac XX:XX:XX:XX:XX:XX   # target a specific device
python -m local_mind_monitor.app --preset p1035            # BrainFlow Athena preset (default p1035)
python -m local_mind_monitor.app --low-latency             # BrainFlow's L1 low-latency mode
```

Click **Connect**, then **Start Recording** to save a `session_*.csv` in the
current directory. **Marker** annotates the next row. Sanity check on real
hardware: alpha power (8–13 Hz) rises when you close your eyes.

### About the Athena preset

BrainFlow's default Athena preset (`p1041`) only delivered an initial burst of
data on our hardware and then stopped streaming. This app defaults to **`p1035`**
(4 EEG channels, matching the Athena's four electrodes), which streams
continuously. If you hit trouble, `--preset p1034` and adding `--low-latency`
are the next things to try.

## Layout

| File | Role |
|---|---|
| `device.py` | BrainFlow session + background acquisition, ring buffers, stall detection / reconnect, 1 Hz recording |
| `processing.py` | Band powers (relative + absolute dB) and signal quality (no GUI dependency) |
| `recorder.py` | Mind Monitor-style CSV writer + markers |
| `single_instance.py` | Single-instance guard (a second launch surfaces the open window) |
| `bluetooth_check.py` | Standalone BLE scanner diagnostic |
| `gui/main_window.py` | Controls, signal-quality row, band readout, wiring |
| `gui/band_readout.py` | Live per-band dB readout cards |
| `gui/plots.py` | pyqtgraph raw-EEG and scrolling band-power widgets |
| `app.py` | Entry point / argument parsing / logging |

## Not yet included (v2 ideas)

OSC/LSL re-broadcast, PPG heart-rate and fNIRS views, local session history,
and a packaged one-file executable.
