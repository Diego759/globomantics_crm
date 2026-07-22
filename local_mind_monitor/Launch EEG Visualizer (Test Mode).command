#!/bin/bash
# macOS test-mode launcher. Runs the app on BrainFlow's synthetic signal
# instead of a real headband -- use it to confirm everything installs and runs
# before your Muse is on hand. Shares the same .venv as the normal launcher.
# The window title will read "(synthetic)".

cd "$(dirname "$0")" || exit 1
SCRIPT_DIR="$(pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
READY_MARKER="$VENV_DIR/.deps-installed"

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
    "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1 || \
        "$VENV_DIR/bin/python" -m ensurepip --upgrade
    "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
    "$VENV_DIR/bin/python" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt" || {
        echo "Dependency install failed. See the error above."
        read -r -p "Press Return to close." _; exit 1; }
    echo "installed" > "$READY_MARKER"
    echo
    echo "Setup complete. Launching in test mode..."
fi

cd "$SCRIPT_DIR/.." || exit 1
"$VENV_DIR/bin/python" -m local_mind_monitor.app --synthetic "$@" >/dev/null 2>&1 &
disown
