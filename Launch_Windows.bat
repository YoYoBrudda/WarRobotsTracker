@echo off
setlocal
cd /d "%~dp0"
title War Robots Screenshot Tracker

echo =====================================
echo War Robots Screenshot Tracker Launcher
echo =====================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on this computer.
  echo Please install Python from https://www.python.org/downloads/
  echo IMPORTANT: During install, check "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv" (
  echo Creating the app environment. This only happens once...
  python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo Starting the tracker app...
python app.py
pause
