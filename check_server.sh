#!/usr/bin/env bash
set -euo pipefail

echo "Checking port 8000..."
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  lsof -nP -iTCP:8000 -sTCP:LISTEN
  echo
  echo "Checking API health..."
  curl -s http://127.0.0.1:8000/api/health
  echo
else
  echo "Nothing is listening on 127.0.0.1:8000."
  echo "Start it with: bash start.sh"
  exit 1
fi
