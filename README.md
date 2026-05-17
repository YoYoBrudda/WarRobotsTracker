# War Robots Screenshot Tracker - Version 11

This is the no-code-friendly desktop version of the War Robots screenshot tracker.

## What V11 does

- Reads War Robots TEAM result screenshots
- Tracks:
  - Win / Loss / Draw
  - Player Name
  - Team Place, from 1 to 6
  - Honor Points
  - Damage + Healing
  - Assists
  - Kills
  - Beacons
- Creates `output/match_log.csv`
- Creates `output/summary.html`
- Adds Average Place to the summary
- Moves duplicate screenshots into the `duplicates` folder instead of processing them twice
- Moves uncertain/wrong-player screenshots into the `needs_review` folder instead of logging bad data
- Saves missing beacon values as `N/A`
- Excludes `N/A` beacon values from Average Beacons


## New in V11

War Robots sometimes shortens very large Damage + Healing values using **M** for million. V11 converts these automatically:

- `10.3M` becomes `10,300,000`
- `10.7 M` becomes `10,700,000`
- `3M` becomes `3,000,000`

This keeps averages accurate in the summary dashboard.

## What changed in V11

V11 keeps the V8 row/manual-name fixes and adds support for damage values shown with M, such as 10.3M or 10.7 M.

- The manual row box is now used only for **Learn name from screenshot**.
- Normal processing tries to find your saved player name in the ally table first.
- If your saved name is a normal English/numeric name and the screenshot does not contain it, the file is moved to `needs_review` instead of being logged incorrectly.
- For symbol-heavy or non-English names, the app can still fall back to **YOUR PLACE ON THE TEAM**.
- If Learn Name cannot OCR the name, it now lets you type and save the name manually.

## How to use on Windows

1. Extract the ZIP.
2. Double-click `Launch_Windows.bat`.
3. Enter your exact in-game name, or click **Learn name from screenshot** after setup.
4. Choose your screenshot folder.
5. For Tesseract, click `Find file` and choose `tesseract.exe`.
6. Click `Save setup`.
7. Click `Process screenshots now`.

## Tesseract note

Tesseract OCR is still required. If it is installed in a folder like:

`C:\Users\YOURNAME\AppData\Local\Programs\Tesseract-OCR`

choose this file inside that folder:

`tesseract.exe`

V11 can also accept the folder itself and will try to find `tesseract.exe` automatically.

## Important screenshot rule

Use screenshots from the War Robots TEAM results screen, with the team table visible.
Do not crop the screenshot manually.

## Duplicate screenshot rule

If the same screenshot is uploaded again, V11 will move it into the `duplicates` folder and skip it.
This works even if the duplicate screenshot has a different filename.

## Needs review folder

If a screenshot probably belongs to a different player, or the app cannot safely read the correct row, V11 moves it into `needs_review`. Check those screenshots manually instead of trusting bad stats.

## Clear saved data

Open the app and click **Clear data** if you want to reset your tracker. The app will ask you to confirm first, so accidental clicks will not erase your saved stats. This clears the CSV, summary page, and duplicate tracking history, but it does not delete your screenshots or processed image files.

---

## V14 executable build note

This version includes files for building a single Windows executable:

- `WarRobotsTracker.spec`
- `build_windows_exe.bat`
- `.github/workflows/build-windows-exe.yml`
- `docs/BUILD_SINGLE_EXE.md`

The final public download should be `WarRobotsTracker.exe`. Tesseract OCR may still need to be installed separately because it is the external OCR engine used by the app.
