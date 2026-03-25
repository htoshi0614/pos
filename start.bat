@echo off
title Girls Bar POS
cd /d "%~dp0"

echo ================================
echo  Girls Bar POS - Starting...
echo ================================
echo.

where py >nul 2>&1
if not errorlevel 1 (
    set "PY=py"
    goto :FOUND
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :FOUND
)
echo [ERROR] Python not found. Please install Python.
pause
exit /b 1

:FOUND
echo [1/3] Checking update...
git pull --ff-only 2>nul
echo.

echo [2/3] Installing packages...
%PY% -m pip install -r requirements.txt -q --disable-pip-version-check 2>nul
echo   Done.
echo.

echo [3/3] Starting server...
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul && start "" http://127.0.0.1:8000/"

%PY% -m uvicorn pos:app --host 0.0.0.0 --port 8000
pause
