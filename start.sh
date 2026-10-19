#!/bin/sh
set -eu
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Creating Python environment..."
  if ! python3 -m venv .venv; then
    rm -rf .venv
    echo "Could not create .venv. On Debian/Ubuntu: sudo apt install python3-venv, then run this again."
    exit 1
  fi
  .venv/bin/python -m pip install -r requirements.txt || { rm -rf .venv; echo "Setup failed, see the messages above."; exit 1; }
fi
if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend packages..."
  npm --prefix frontend install
fi

.venv/bin/python -m backend.app &
backend=$!
trap 'kill "$backend" 2>/dev/null || true; wait' EXIT
trap 'exit 130' INT TERM
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort --open
