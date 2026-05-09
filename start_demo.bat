@echo off
title POSStart Demo

set NGROK_DOMAIN=concentric-unofficiously-arabella.ngrok-free.dev
set PORT=8000
set WORK_DIR=%~dp0.claude\worktrees\dazzling-knuth

echo === POSStart Demo ===
echo.

echo [1/3] Demo data setup...
cd /d "%~dp0"
python demo_seed.py
echo.

echo [2/3] Starting POS server...
start "POSStart Server" /min cmd /c "cd /d ""%WORK_DIR%"" && python -m uvicorn pos:app --host 0.0.0.0 --port %PORT% --reload"
timeout /t 4 /nobreak > nul

echo [3/3] Starting ngrok...
echo Domain: https://%NGROK_DOMAIN%
echo.

"%~dp0ngrok.exe" http --domain=%NGROK_DOMAIN% %PORT%
