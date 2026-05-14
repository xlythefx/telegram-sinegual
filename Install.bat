@echo off
REM ============================================================
REM  Sinegualerts bot — one-time setup
REM
REM  Double-click this AFTER:
REM    1. Python 3.12 is installed (from python.org)
REM    2. You're inside the cloned/downloaded telegram-bot folder
REM
REM  This script will:
REM    - Verify Python 3.12 is available
REM    - Create a fresh .venv using Python 3.12
REM    - Install all required packages
REM    - Open .env in Notepad so you can paste your secrets
REM
REM  When this finishes, double-click "Run Service.bat" to start.
REM ============================================================

cd /d "%~dp0"

echo.
echo === Sinegualerts bot installer ===
echo.

REM ---- 1. Check Python 3.12 is available -------------------------
echo Checking for Python 3.12...
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python 3.12 is not installed.
    echo.
    echo Download it here:
    echo   https://www.python.org/downloads/release/python-3128/
    echo.
    echo During install, CHECK the box "Add python.exe to PATH".
    echo Then run this Install.bat again.
    echo.
    pause
    exit /b 1
)
py -3.12 --version

REM ---- 2. Remove any old .venv ----------------------------------
if exist ".venv" (
    echo.
    echo Removing old .venv ...
    rmdir /s /q ".venv"
)

REM ---- 3. Create fresh venv with Python 3.12 -------------------
echo.
echo Creating .venv with Python 3.12 ...
py -3.12 -m venv .venv
if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
)

REM ---- 4. Install requirements ---------------------------------
echo.
echo Installing packages (this can take 1-3 minutes) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\pip.exe" install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet connection and try again.
    pause
    exit /b 1
)

REM ---- 5. Verify the critical imports work ---------------------
echo.
echo Verifying installation ...
".venv\Scripts\python.exe" -c "import telegram, anthropic, dotenv, pymysql, yfinance, pypdf; print('All modules OK')"
if errorlevel 1 (
    echo [ERROR] Some modules failed to import.
    pause
    exit /b 1
)

REM ---- 6. Set up .env -------------------------------------------
if not exist ".env" (
    echo.
    echo Creating .env from .env.example ...
    copy ".env.example" ".env" >nul
    echo.
    echo Opening .env in Notepad. PASTE YOUR SECRETS and save:
    echo   - TELEGRAM_BOT_TOKEN
    echo   - ANTHROPIC_API_KEY
    echo   - DB credentials
    echo   - TELEGRAM_CHANNEL_ID  (public channel)
    echo   - TELEGRAM_ADMIN_CHAT_ID  (admin group)
    echo   - TELEGRAM_ADMIN_USER_IDS  (your Telegram user id)
    echo.
    pause
    notepad ".env"
) else (
    echo.
    echo .env already exists - skipping. Edit it manually if needed.
)

REM ---- Done -----------------------------------------------------
echo.
echo ============================================================
echo  Install complete.
echo.
echo  Next step: double-click "Run Service.bat" to start the bot.
echo ============================================================
echo.
pause
