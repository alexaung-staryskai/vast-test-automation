#!/usr/bin/env bash
# Start VAST Test Bench (macOS / Linux).
#   ./run.sh                          pick or create a test site in the browser
#   ./run.sh sites/acme-poc           open that test site directly (any folder with a config.yaml)
#   ./run.sh --port 8081              another port (default: $PORT, the site's app.port, or 8080)
#   ./run.sh --no-browser             don't open the browser
# Stop it with Ctrl+C here, or ./stop.sh from another terminal.
set -euo pipefail
cd "$(dirname "$0")"

# the newest Python 3.10+ on this machine
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "VAST Test Bench needs Python 3.10 or newer, and none was found." >&2
  echo "  macOS:          brew install python   (or https://www.python.org/downloads/)" >&2
  echo "  Ubuntu/Debian:  sudo apt install python3 python3-venv" >&2
  echo "  RHEL/Rocky:     sudo dnf install python3.11" >&2
  exit 1
fi
exec "$PY" start.py "$@"
