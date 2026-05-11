@echo off
REM Always-on Sinegualerts bot service.
REM Handles /daily /weekly /monthly /gold /greeting chat commands AND fires
REM scheduled posts (daily 23:00, weekly Sat 06:00, monthly last day 23:00,
REM gold every 8h). All times Asia/Manila.
cd /d "%~dp0"
".venv\Scripts\python.exe" bot.py poll
pause
