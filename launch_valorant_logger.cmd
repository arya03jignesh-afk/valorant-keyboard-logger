@echo off
setlocal
cd /d "%~dp0"
py valorant_keyboard_logger.py
if errorlevel 1 (
    echo.
    echo Failed to launch the logger.
    echo Ensure Python 3.10+ is installed and available on PATH.
    pause
)
