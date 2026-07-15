#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p logs

echo "Starting Integration Dashboard Backend..."
echo "Project: $(pwd)"
echo "Log: $(pwd)/logs/server.log"

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

"$PYTHON_BIN" -u server.py 2>&1 | tee logs/server.log
