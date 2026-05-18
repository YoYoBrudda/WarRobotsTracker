# War Robots Screenshot Tracker - Version 16

This is the EXE-friendly version of the War Robots screenshot tracker.

## What it does

- Reads War Robots TEAM result screenshots
- Tracks win/loss/draw, player name, team place, honor points, damage + healing, assists, kills, and beacons
- Supports damage values like `10.3M` by converting them to real numbers
- Creates a `match_log.csv` and `summary.html`
- Moves duplicate screenshots to `duplicates`
- Moves uncertain screenshots to `needs_review`
- Lets you clear saved tracker data with a confirmation prompt

## New in V16

The app no longer depends on folders sitting next to the program file.

On first launch, it creates a tracker folder here by default:

`the same folder as WarRobotsTracker.exe/`

Inside that folder, it creates:

```text
screenshots/
processed/
duplicates/
needs_review/
output/
```

You can also choose a different **Tracker data folder** inside the app. This is the folder where all tracker files will live.

Settings are saved in your Windows AppData folder, so your setup stays saved even when you download a newer version of the app.

## How to use

1. Open `WarRobotsTracker.exe`.
2. Choose a **Tracker data folder**, or keep the default.
3. Put War Robots result screenshots into the `screenshots` folder.
4. Enter your in-game name, or use **Learn name from screenshot**.
5. Select your Tesseract OCR path if needed.
6. Click **Process screenshots now**.
7. Click **Open summary** to view your stats.

## Tesseract OCR

Tesseract OCR is still required.

If Tesseract is installed at:

`C:\Users\YOURNAME\AppData\Local\Programs\Tesseract-OCR`

choose:

`C:\Users\YOURNAME\AppData\Local\Programs\Tesseract-OCR\tesseract.exe`

The app can also usually accept the folder itself and find `tesseract.exe` automatically.

## Notes

- Use full War Robots TEAM result screenshots.
- Use PNG screenshots when possible.
- Avoid compressed Discord/YouTube screenshots when possible.
- If a file goes to `needs_review`, the app was not confident enough to log it safely.
