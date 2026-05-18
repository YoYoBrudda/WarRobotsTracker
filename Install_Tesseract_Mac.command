#!/bin/bash
echo "This installer uses Homebrew."
if ! command -v brew &> /dev/null; then
  echo "Homebrew is not installed. Opening the Homebrew website..."
  open "https://brew.sh"
  echo "Install Homebrew first, then run this file again."
  read -p "Press Enter to close."
  exit 1
fi
brew install tesseract
echo "Done. Restart the War Robots Tracker after installation."
read -p "Press Enter to close."
