#!/usr/bin/env bash
# macOS: double-click this file in Finder to stop VAST Test Bench.
cd "$(dirname "$0")" && ./stop.sh "$@"
echo; read -r -p "Press Enter to close this window..." _
