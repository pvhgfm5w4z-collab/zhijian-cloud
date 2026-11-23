@echo off
chcp 65001 >nul
title Zhijian Cloud - Service (port 8321)
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found in: %~dp0
  echo Please make sure you run this file inside the project folder.
  pause
  exit /b 1
)

echo ==========================================
echo  Starting service: http://127.0.0.1:8321
echo  Tool mode  : /tool/compare
echo  Agent mode : /agent
echo  Keep this window OPEN. Press Ctrl+C to stop.
echo ==========================================
echo.

".venv\Scripts\python.exe" demo_app.py

echo.
echo Service stopped.
pause
