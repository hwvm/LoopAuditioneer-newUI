@echo off
setlocal
cd /d "%~dp0"
python -c "import numpy" >nul 2>&1
if errorlevel 1 (
  echo Installing the audio analysis dependency...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Install Python 3.10 or newer, then try again.
    pause
    exit /b 1
  )
)
python server.py
if errorlevel 1 pause
