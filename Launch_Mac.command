#!/bin/bash
cd "$(dirname "$0")"
echo "====================================="
echo "War Robots Screenshot Tracker Launcher"
echo "====================================="
echo

if ! command -v python3 &> /dev/null; then
  echo "Python 3 was not found. Install it from https://www.python.org/downloads/"
  read -p "Press Enter to close."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Creating the app environment. This only happens once..."
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo
echo "Starting the tracker app..."
python app.py
