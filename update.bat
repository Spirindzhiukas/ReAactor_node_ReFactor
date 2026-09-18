@echo off
cd /d "%~dp0"
echo [ReFactor] Updating ReActor ReFactor from the repository...
git pull --ff-only
echo.
echo [ReFactor] If dependencies changed, re-run install.bat (or: python install.py --yes).
@pause
