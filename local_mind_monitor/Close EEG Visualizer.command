#!/bin/bash
# macOS rescue: force-closes any running EEG Visualizer. You normally never
# need this -- the app surfaces its existing window instead of stacking copies
# and shuts down cleanly. Keep it for the rare case a launch seems stuck.

echo "Closing any running EEG Visualizer..."
if pkill -f "local_mind_monitor.app"; then
    echo "Closed."
else
    echo "Nothing running -- all clear."
fi
sleep 1
