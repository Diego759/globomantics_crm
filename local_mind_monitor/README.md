# EEG Visualizer for Muse

A private, fully-local desktop app that visualizes and records EEG from the
**Muse S Athena** headband. Connects over native Bluetooth via
[BrainFlow](https://brainflow.org), shows live EEG and per-channel band powers,
and records CSV sessions with markers. Nothing leaves your machine — no
accounts, no cloud. Runs on **Windows and macOS**.

## Get the code

```bash
git clone https://github.com/Diego759/EEG_visualizer_for_muse.git
```

The app lives in the **`local_mind_monitor/`** subfolder of the repo — that's
where the launchers below live.

## Features

- **Live band-power readout** — a colour-coded row of cards (δ Delta / θ Theta /
  α Alpha / β Beta / γ Gamma) showing each band's current absolute power in dB.
- **Scrolling charts** — raw EEG per electrode (TP9, AF7, AF8, TP10) plus a
  scrolling band-power (dB) graph with each band's Greek symbol riding the end
  of its line.
- **Signal quality** — per-electrode good/ok/bad contact indicators.
- **CSV recording + markers** — ~1 Hz rows in a spreadsheet-friendly column
  layout, with a marker button that annotates the session.
- **Robust connection** — detects a stalled Bluetooth stream, auto-reconnects,
  and tells you in the status bar instead of freezing silently. All activity is
  logged to `local_mind_monitor.log` next to the app.
- **The Chamber** — binaural / monaural / isochronic entrainment journeys whose
  frequency is steered by your own live band powers, with a response report at
  the end and WAV export. See [The Chamber](#the-chamber) below.

## Windows: just double-click

Double-click **`Launch EEG Visualizer.bat`** inside `local_mind_monitor\`. The
first double-click installs everything into a private `.venv` folder and takes
about a minute — every double-click after that opens the app straight away.
Requires Python 3.11+ from [python.org](https://python.org/downloads/) (check
"Add python.exe to PATH" during install) if you don't already have it.

## macOS: just double-click

Double-click **`Launch EEG Visualizer.command`** inside `local_mind_monitor/`.
Same idea: the first run builds a private `.venv` and installs dependencies,
later runs launch instantly. Requires Python 3.11+ from
[python.org](https://python.org/downloads/) (or `brew install python`).

The first time, macOS Gatekeeper may block a downloaded `.command`. If
double-click does nothing, **right-click → Open** once (or run
`chmod +x "Launch EEG Visualizer.command"` in Terminal). macOS will also ask for
**Bluetooth permission** the first time the app scans for the headband — allow it.

## Try it without a headband

To confirm it installs and runs before your Muse is on hand, use the test-mode
launcher — **`Launch EEG Visualizer (Test Mode).bat`** (Windows) or
**`Launch EEG Visualizer (Test Mode).command`** (macOS). It runs on a synthetic
signal (the window title shows "(synthetic)") and shares the same `.venv`, so
it's instant once either launcher has been run once.

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

If a launch ever seems stuck, **`Close EEG Visualizer`** (`.bat` / `.command`)
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
python -m local_mind_monitor.app --battery-scale 1.0       # show BrainFlow's raw battery number
```

Click **Connect**, then **Start Recording** to save a `session_*.csv` in the
current directory. **Marker** annotates the next row. Sanity check on real
hardware: alpha power (8–13 Hz) rises when you close your eyes.

### About the battery percentage

BrainFlow reports the Athena's battery as `raw / 512`
(`MUSE_ATHENA_BATTERY_PERCENT_SCALE_FACTOR` in its `muse_athena.cpp`, a constant
carried over from the older Muse telemetry format). The Athena actually encodes
percent as `raw / 256` (Q8.8 fixed point), so BrainFlow's number comes out
exactly **half** — a fully charged headband reads 50%. This app doubles it to get
the true percentage. Each new reading is written to `local_mind_monitor.log` as
`Battery: raw=... x2.00 -> ...%`, and `--battery-scale 1.0` shows BrainFlow's
uncorrected value if you want to compare.

### About the Athena preset

BrainFlow's default Athena preset (`p1041`) only delivered an initial burst of
data on our hardware and then stopped streaming. This app defaults to **`p1035`**
(4 EEG channels, matching the Athena's four electrodes), which streams
continuously. If you hit trouble, `--preset p1034` and adding `--low-latency`
are the next things to try.

## The Chamber

A binaural-beats player that closes the loop around the headband.

The App Store is full of binaural apps, and every one of them is **open loop**:
it plays 10 Hz at you and assumes something happened. The evidence that a fixed
beat entrains cortical rhythms is genuinely mixed, and the studies that do find
an effect find it is *personal* — which is an argument for measuring rather than
believing. You are already wearing an EEG headband, so measure.

Open the **Chamber** tab, pick a journey, tick *Steer with my EEG* and press
Start:

1. **Baseline** — 60 seconds of ambience and no tones, to measure your resting
   rhythm in the band this journey targets.
2. **Play** — the journey glides carrier and beat frequency through its
   segments; the target band is derived from the frequency actually playing,
   using the same band edges the analyser uses.
3. **Search** — the beat is held at a candidate frequency for ~45 s, scored as
   *standard deviations of target-band power above your own rest*, and stepped
   toward whatever actually moved. It climbs from the best frequency found so
   far, and settles once both neighbours have lost. Poor electrode contact
   freezes the search rather than teaching it nonsense.
4. **Report** — best frequency, how far above rest it got, and your personal
   response curve across every frequency tried. Every sample is written to
   `chamber_*.csv` next to the session recordings.

```
Alpha:
  best beat 9.60 Hz — +2.31 SD above rest (clear response)
  response curve: 8.80Hz +0.42  9.20Hz +1.55  9.60Hz +2.31  10.00Hz +0.12
```

### Journeys

Twelve are built in — `first-descent`, `theta-gateway`, `hypnagogic-hold`,
`deep-delta`, `power-nap`, `schumann`, `focus-beta`, `gamma-spark`,
`astral-primer`, `golden-528`, `calm-reset`, `open-room` — and they are plain
data in `chamber/journeys.py`, not purchasable content. Drop your own JSON in
`~/.eeg-visualizer/journeys/` and it appears in the picker next to the
built-ins:

```json
{
  "key": "my-descent", "title": "My Descent", "summary": "20 minutes to 5.5 Hz.",
  "mode": "binaural", "ambience_kind": "pink",
  "segments": [
    {"name": "Settle", "seconds": 300, "beat_start": 10.0, "carrier_start": 205.0},
    {"name": "Down", "seconds": 900, "beat_start": 10.0, "beat_end": 5.5,
     "carrier_start": 205.0, "carrier_end": 175.0, "ambience": 0.4}
  ]
}
```

Carrier choices follow Oster's perception work — binaural beats are clearest on
carriers in the low hundreds of Hz, lower for lower beats.

### Three modes

| Mode | How it works | Needs |
|---|---|---|
| Binaural | each ear gets its own tone; the beat exists only in your head | headphones |
| Monaural | both tones in both ears; they beat acoustically | anything |
| Isochronic | one tone gated on and off at the beat rate | anything |

### Command line

The Chamber runs without the GUI, and everything except `play` runs without
audio hardware or a headband:

```bash
python -m local_mind_monitor.chamber list
python -m local_mind_monitor.chamber show theta-gateway
python -m local_mind_monitor.chamber play first-descent --adaptive
python -m local_mind_monitor.chamber play --beat 7.83 --minutes 20   # ad-hoc hold
python -m local_mind_monitor.chamber render deep-delta -o deep-delta.wav
```

### "Is there an Android version?"

No — and there doesn't need to be. **Export WAV…** (or `render`) writes an
ordinary 16-bit stereo WAV you can copy to any phone and play offline forever,
in any player, with no app and no account. After an adaptive session the export
carries *your* tuning:

```bash
python -m local_mind_monitor.chamber render first-descent --tune 'Alpha:-0.4' \
    -o first-descent-tuned.wav
```

### Safety

Entrainment audio is not a medical device and nothing here treats anything. Do
not run a journey while driving or operating machinery — `deep-delta`,
`power-nap` and `hypnagogic-hold` are built to put you to sleep. If you have
epilepsy or a seizure disorder, talk to a doctor first: photic entrainment is a
known seizure trigger and the auditory case is not well characterised. Start at
a low volume; tones sit 12 dB below full scale by design, and the output is
hard-limited, but your headphone amp is not.

## Layout

| File | Role |
|---|---|
| `device.py` | BrainFlow session + background acquisition, ring buffers, stall detection / reconnect, 1 Hz recording |
| `processing.py` | Band powers (relative + absolute dB) and signal quality (no GUI dependency) |
| `recorder.py` | CSV session writer + markers |
| `single_instance.py` | Single-instance guard (a second launch surfaces the open window) |
| `bluetooth_check.py` | Standalone BLE scanner diagnostic |
| `bands.py` | Band names, symbols and frequency edges (shared, dependency-free) |
| `chamber/journeys.py` | Journeys as data: segments, glides, the built-in library, JSON |
| `chamber/synth.py` | Click-free binaural / monaural / isochronic synthesis (numpy only) |
| `chamber/adaptive.py` | The closed loop: baseline, response scoring, frequency search |
| `chamber/session.py` | Journey clock + synth + loop + per-sample CSV log |
| `chamber/render.py` | Offline WAV export (standard library + numpy) |
| `chamber/player.py` | Live audio output via sounddevice |
| `chamber/cli.py` | `python -m local_mind_monitor.chamber` |
| `gui/main_window.py` | Controls, signal-quality row, band readout, tabs, wiring |
| `gui/chamber_panel.py` | The Chamber tab: picker, live readout, timeline, export |
| `gui/band_readout.py` | Live per-band dB readout cards |
| `gui/plots.py` | pyqtgraph raw-EEG and scrolling band-power widgets |
| `app.py` | Entry point / argument parsing / logging |

## Tests

The signal processing, journeys, closed loop, WAV export and CLI are covered by
a headless test suite — no headband, no audio device, no display required:

```bash
pip install pytest
python -m pytest tests/
```

## Not yet included (v2 ideas)

OSC/LSL re-broadcast, PPG heart-rate and fNIRS views, local session history,
a packaged one-file executable, and closing the Chamber's loop on amplitude as
well as frequency.
