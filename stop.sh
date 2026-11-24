#!/usr/bin/env bash
# Stop VAST Test Bench (macOS / Linux).
#   ./stop.sh               stop every instance started with run.sh / run.command
#   ./stop.sh --port 8081   stop only the one on that port
set -euo pipefail
cd "$(dirname "$0")"
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.10 or newer was not found." >&2; exit 1; }
exec "$PY" start.py --stop "$@"
