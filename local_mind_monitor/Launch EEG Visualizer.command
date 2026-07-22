#!/bin/bash
# macOS double-click launcher. First run creates a local virtual environment
# and installs dependencies (shown in this window); every run after that skips
# straight to launching the app. If double-clicking doesn't run it, right-click
# -> Open the first time, or run: chmod +x "Launch EEG Visualizer.command"

cd "$(dirname "$0")" || exit 1
SCRIPT_DIR="$(pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
# Written only after deps install successfully, so a half-finished setup is
# retried next time instead of launching a broken app.
READY_MARKER="$VENV_DIR/.deps-installed"

# Find a Python 3 interpreter.
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo "Python 3 was not found on this Mac."
    echo "Install it from https://python.org/downloads/ (or run: brew install python),"
    echo "then double-click this file again."
    read -r -p "Press Return to close." _
    exit 1
fi

# A half-made venv (bin/ present but no pyvenv.cfg) can't be used; rebuild it.
if [ -d "$VENV_DIR/bin" ] && [ ! -f "$VENV_DIR/pyvenv.cfg" ]; then
    echo "Repairing a damaged setup folder..."
    rm -rf "$VENV_DIR"
fi

if [ ! -f "$READY_MARKER" ]; then
    echo "Setting up EEG Visualizer for the first time..."
    echo "This installs its dependencies into a private folder here (.venv)"
    echo "and only happens once. It can take a minute."
    echo
    if [ ! -f "$VENV_DIR/pyvenv.cfg" ]; then
        "$PY" -m venv "$VENV_DIR" || {
            echo "Could not create the virtual environment. See the error above."
            read -r -p "Press Return to close." _; exit 1; }
    fi
    # Some Pythons come up without pip in the venv; make sure it's there.
    "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1 || \
        "$VENV_DIR/bin/python" -m ensurepip --upgrade
    "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
    "$VENV_DIR/bin/python" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt" || {
        echo "Dependency install failed. See the error above."
        read -r -p "Press Return to close." _; exit 1; }
    echo "installed" > "$READY_MARKER"
    echo
    echo "Setup complete. Launching..."
fi

# local_mind_monitor is invoked as a module, so run from the folder that
# CONTAINS local_mind_monitor/ (one level up from here). Launch detached so
# closing this Terminal window doesn't close the app; errors go to the log
# file next to the app (local_mind_monitor.log).
cd "$SCRIPT_DIR/.." || exit 1
"$VENV_DIR/bin/python" -m local_mind_monitor.app "$@" >/dev/null 2>&1 &
disown
