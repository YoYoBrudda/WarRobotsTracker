# Build Single EXE

This project includes a GitHub Actions workflow that builds `WarRobotsTracker.exe` on Windows.

1. Upload this project to GitHub.
2. Go to the **Actions** tab.
3. Run **Build Windows EXE**.
4. Download the `WarRobotsTracker-Windows` artifact.
5. Publish the resulting ZIP or EXE in GitHub Releases.

V15.1 stores user settings in AppData and creates tracker data folders in the user's chosen folder, so the EXE can be distributed as a standalone app.
