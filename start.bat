@echo off
cd /d %~dp0
python server.py
if errorlevel 1 (
    echo.
    echo Start failed. Install dependencies with: pip install -e .
    pause
)
