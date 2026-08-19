# CLAUDE.md

Guidance for AI assistants working in this repository.

## What this repo is

**EEG Visualizer for Muse** — a private, fully-local **desktop app** (PySide6 +
pyqtgraph) that connects to a **Muse S Athena** headband over native Bluetooth
LE via [BrainFlow](https://brainflow.org), shows live raw EEG / band powers /
signal quality / battery / heart rate, and records Mind Monitor-compatible CSV
sessions. Nothing leaves the machine — no network calls, no accounts, no cloud.
Targets **Windows and macOS**, Python **3.11+**.

The whole app lives in `local_mind_monitor/`. The repo root also carries an
unrelated **legacy Flask "Globomantics CRM" training project** (`start.py`,
`database.py`, `templates/`, `static/`, `data/`, `Dockerfile`,
`.devcontainer/`, root `requirements.txt` which contains only `flask`). That
legacy code is kept for history — **do not modify it, and do not confuse its
`requirements.txt` with the app's.** Any request about "the app", EEG, Muse,
recording, or plots means `local_mind_monitor/`.

## Layout

```
local_mind_monitor/
  app.py               entry point: argparse, file logging, single-instance guard, Qt bootstrap
  device.py            BrainFlow session, background acquisition thread, ring buffers,
                       stall detection + reconnect, battery/PPG, 1 Hz recording driver
  processing.py        band powers (relative + absolute dB), heart rate, signal quality
  recorder.py          CSV session writer + markers (Mind Monitor column layout)
  single_instance.py   QLocalServer-based single-instance guard (fails open)
  bluetooth_check.py   standalone BLE scanner diagnostic (bleak)
  gui/main_window.py   controls, status bar, vitals, wiring, 10 Hz refresh timer
  gui/band_readout.py  the five live δ/θ/α/β/γ dB cards
  gui/plots.py         pyqtgraph raw-EEG + scrolling band-power widgets, colour palette
  requirements.txt     the app's real dependencies
  *.bat / *.command    double-click launchers (Windows / macOS)
  README.md            user-facing documentation — keep in sync with behaviour changes
```

Root `README.md` is the landing page for the same app; `local_mind_monitor/README.md`
is the detailed one. Both need updating when user-visible behaviour or CLI flags change.

## Architecture

Three layers, deliberately separated:

1. **`processing.py` has no GUI and no device dependency** — pure functions over
   numpy arrays. Keep it that way: it is the only part that can be exercised
   headless, and it is where signal-processing changes belong.
2. **`device.py` (`MuseDevice`) has no GUI dependency.** It owns a background
   daemon thread (`_loop`, ~20 Hz) that pulls BrainFlow data into ring buffers,
   watches for stalls, reconnects, and drives the 1 Hz recorder. The GUI never
   blocks on it — it only reads snapshots.
3. **`gui/`** polls the device on a `QTimer` at 10 Hz (raw plot every tick;
   band powers, signal quality and vitals every 5th tick ≈ 2 Hz, because the
   FFT work is expensive on the GUI thread).

### Threading rules (important)

- `_lock` guards the EEG/PPG ring buffers.
- `_board_lock` serialises **all** BrainFlow board lifecycle calls
  (`prepare_session` / `start_stream` / `stop_stream` / `release_session` /
  `get_board_data`). The recovery path and a `Disconnect`/close on another
  thread must never touch the native session concurrently.
- `_compute_lock` serialises `DataFilter` calls — band powers run on both the
  GUI thread and the recording thread, and BrainFlow's `DataFilter` is not
  thread-safe.

Any new BrainFlow or DataFilter call must take the matching lock.

### Hard-won behaviours — do not "simplify" these away

Each of these fixes a real failure; the code comments explain why. Preserve the
behaviour (and the comments) unless explicitly asked to change it.

- **Preset `p1035`, not BrainFlow's default `p1041`.** `p1041` delivered only an
  initial burst then stopped streaming on the Athena. `p1035` = 4 EEG channels
  matching TP9/AF7/AF8/TP10.
- **`low_latency` defaults to false.** BrainFlow's conservative mode is stabler
  for long recordings; `low_latency=true` stalled the stream.
- **Battery is doubled (`ATHENA_BATTERY_SCALE = 2.0`).** BrainFlow scales the
  Athena's raw reading by 1/512 while the device encodes Q8.8 (1/256), so a full
  charge reads 50%. `--battery-scale 1.0` shows BrainFlow's raw value.
- **Reconnect never gives up.** After `MAX_RECOVERY_ATTEMPTS` the UI says
  "signal lost", but retries continue for the whole session with exponential
  backoff capped at 60 s — a dropped link 12 minutes into a 40-minute session
  used to silently cost the rest of the recording.
- **Stale rows are never recorded.** `_maybe_record` skips writing while no new
  EEG has arrived since the last row; the ring buffer would otherwise emit
  byte-identical rows that look real but are a frozen value.
- **`_release_board` runs on a daemon thread with a timeout.** A hung native
  `release_session()` used to leave `pythonw.exe` alive after the window closed.
- **The single-instance guard fails open.** It must never be the reason a launch
  does nothing.
- **Logging never blocks startup**, and `sys.excepthook` routes uncaught
  exceptions to the log file — under `pythonw` there is no console.

## Running and developing

There is **no test suite, no linter config, and no CI** in this repo. Verification
is manual. Do not invent a `pytest`/`ruff` setup unless asked.

```bash
# from the repo root (the package is run as a module, so cwd must contain local_mind_monitor/)
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r local_mind_monitor/requirements.txt

python -m local_mind_monitor.app --synthetic     # synthetic signal, no hardware needed
python -m local_mind_monitor.app                 # real Muse S Athena
python -m local_mind_monitor.bluetooth_check     # BLE scan diagnostic
```

CLI flags: `--synthetic`, `--mac ADDR`, `--preset p1035`, `--low-latency`,
`--battery-scale 2.0`.

### Verifying changes without a headband or a display

This environment has no Bluetooth, no Muse, and no X display, so the GUI cannot
be launched here. What *can* be checked:

```bash
pip install brainflow numpy          # enough for processing.py and recorder.py
python -m compileall local_mind_monitor
python -c "from local_mind_monitor import processing"   # import check
```

`processing.py` and `recorder.py` run headless against synthetic numpy arrays —
exercise them directly with a throwaway script when you touch band powers, heart
rate, signal quality, or the CSV layout. `device.py` and everything in `gui/`
require BrainFlow's native stack and Qt; review those by reading, and say plainly
that they were not run.

Runtime detail: the app writes `local_mind_monitor/local_mind_monitor.log`
(rotating, 1 MB × 3) and `session_*.csv` into the **current working directory**.
Both are gitignored — never commit them.

## Conventions

- Python 3.11+, `from __future__ import annotations`, PEP 604 unions
  (`float | None`), type hints on public methods.
- Module docstrings explain the module's role; comments explain **why**, usually
  citing the failure they prevent. Match that density — this codebase is
  deliberately comment-heavy about hardware quirks.
- 4-space indent, ~100 column soft limit, double quotes.
- Loggers are named `local_mind_monitor.<area>` (`.device`, `.gui`).
- Exceptions around hardware/BLE/logging are caught broadly and logged rather
  than propagated — the app must never die because a peripheral misbehaved.
- Qt styling lives in the `STYLESHEET` constant in `gui/main_window.py`; band
  colours in `BAND_COLORS` in `gui/plots.py`. Keep the dark theme consistent.
- Band order is always `Delta, Theta, Alpha, Beta, Gamma` — `BANDS`,
  `BAND_SYMBOLS`, `BAND_RANGES`, and `BAND_COLORS` are index-aligned. Changing
  one means changing all four and the CSV header.
- CSV compatibility: space-separated timestamp (not ISO `T`), numeric HSI codes
  (1 good / 2 ok / 4 bad), `Elements` marker column last. Existing columns must
  not be renamed or reordered — external parsers depend on the layout; add new
  columns before `Elements`.

### Launcher scripts

`.bat` (CRLF) and `.command` (LF) line endings are enforced by
`local_mind_monitor/.gitattributes` — a `.command` committed with CRLF will not
run on macOS. The two platforms' launchers are near-duplicates by design; a fix
to one almost always belongs in the other (normal + test-mode variants each).
They gate setup on the `.venv/.deps-installed` marker, not on `pyvenv.cfg`, so a
half-finished install is retried rather than launching a broken app.

## Git workflow

- Commit subjects are sentence-case and say what changed and why it matters
  (e.g. "Don't record a dead stream: skip stale rows while the BLE link is
  stalled"); docs-only commits use a `docs:` prefix.
- Default branch is `master`. Work on the branch you were assigned; do not push
  to `master` and do not open a PR unless explicitly asked.
- Never commit `session_*.csv`, `*.log`, `.venv/`, or `__pycache__/`.
