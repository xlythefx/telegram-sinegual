@echo off
REM ============================================================
REM  Sinegualerts bot — pull updates from GitHub
REM
REM  Double-click this whenever there's a new version to deploy.
REM  It will:
REM    - Stop the running bot (you'll need to close its window first)
REM    - git pull the latest code
REM    - Reinstall any new dependencies
REM
REM  Then double-click "Run Service.bat" to restart.
REM ============================================================

cd /d "%~dp0"

echo.
echo === Sinegualerts bot updater ===
echo.
echo  Make sure the bot service window is CLOSED before continuing.
echo.
pause

echo.
echo Pulling latest code from GitHub ...
git pull
if errorlevel 1 (
    echo.
    echo [ERROR] git pull failed. Resolve conflicts manually then re-run.
    pause
    exit /b 1
)

echo.
echo Reinstalling dependencies (in case requirements.txt changed) ...
".venv\Scripts\pip.exe" install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Update complete.
echo.
echo  Next: double-click "Run Service.bat" to restart the bot.
echo ============================================================
echo.
pause
