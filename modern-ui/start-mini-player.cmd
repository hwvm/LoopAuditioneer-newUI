@echo off
setlocal
cd /d "%~dp0"
if not exist ".workspace\academie\player\library.json" (
  echo Prepare the recordings first:
  echo python prepare_academie.py --source "D:\Audio\0_Academie"
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8766/player if the player is already running.
python server.py --workspace .workspace/academie --port 8766 --player
if errorlevel 1 pause
