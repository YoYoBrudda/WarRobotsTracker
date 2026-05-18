@echo off
setlocal
cd /d "%~dp0"
echo Building WarRobotsTracker.exe...

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Please install Python 3.11+ first.
  pause
  exit /b 1
)

python -m venv .buildvenv
call .buildvenv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm WarRobotsTracker.spec

echo.
echo Done. Your exe should be here:
echo dist\WarRobotsTracker.exe
echo.
pause
