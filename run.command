#!/usr/bin/env bash
# macOS: double-click this file in Finder to start VAST Test Bench (opens a Terminal window).
# Close the window or press Ctrl+C to stop it - or double-click stop.command.
cd "$(dirname "$0")" && ./run.sh "$@"
status=$?
if [ $status -ne 0 ]; then echo; read -r -p "Press Enter to close this window..." _; fi
exit $status
