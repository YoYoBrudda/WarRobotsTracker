#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 &> /dev/null; then
  echo "Python 3 was not found. Install it using your package manager."
  exit 1
fi
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
