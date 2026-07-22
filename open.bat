@echo off
chcp 65001 >nul 2>&1
title DawnAssetHelper

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create venv. Make sure Python 3.11+ is installed and in PATH.
        pause
        exit /b 1
    )
)

echo Checking dependencies...
venv\Scripts\python.exe -m pip install -q -r requirements.txt

echo Starting DawnAssetHelper web server...
echo Open http://127.0.0.1:8000 in your browser.
start "" http://127.0.0.1:8000
venv\Scripts\python.exe server\app.py
pause
