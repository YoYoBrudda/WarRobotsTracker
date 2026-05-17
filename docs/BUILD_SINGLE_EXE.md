# Building the single-file Windows executable

This project is ready to build into `WarRobotsTracker.exe`.

## Easiest path: GitHub Actions

1. Upload this folder to a GitHub repository.
2. Go to the repository's **Actions** tab.
3. Open **Build Windows EXE**.
4. Click **Run workflow**.
5. Download the finished artifact: `WarRobotsTracker-Windows.zip`.
6. Upload that ZIP or the included `WarRobotsTracker.exe` to your GitHub Releases page.

## Local Windows build

Double-click:

`build_windows_exe.bat`

The built executable will appear at:

`dist/WarRobotsTracker.exe`

## Important OCR note

The executable bundles the Python app and Python packages, but Tesseract OCR is still a separate OCR engine.
Users may still need to install Tesseract OCR using `Install_Tesseract_Windows.ps1`, then select the Tesseract path in the app if it is not auto-detected.
