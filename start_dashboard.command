#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
bash start.sh
echo
echo "Server stopped. Press Enter to close this window."
read -r _
