@echo off
setlocal enabledelayedexpansion
title IngestOps - Operations Cockpit UI Server

:: Detect Python executable
set "PYTHON_CMD="

:: 1. Check direct python command
python --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_CMD=python"
    goto :START_SERVER
)

:: 2. Check py launcher
py -3.11 --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_CMD=py -3.11"
    goto :START_SERVER
)

:: 3. Check specific Python 3.11 install directory
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto :START_SERVER
)

:: 4. Check Program Files
if exist "%ProgramFiles%\Python311\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python311\python.exe"
    goto :START_SERVER
)

:PYTHON_NOT_FOUND
color 0C
echo ==============================================================================
echo [ERROR] Python 3.11+ was not found on your system PATH!
echo ==============================================================================
pause
exit /b 1

:START_SERVER
color 0B
cls
echo ==============================================================================
echo          INGESTOPS - OPERATIONS COCKPIT (CLOUD & SAGE UI)
echo ==============================================================================
echo  Python Runtime: !PYTHON_CMD!
echo  Cockpit UI URL: http://127.0.0.1:8000
echo  API Docs / API: http://127.0.0.1:8000/docs
echo ==============================================================================
echo.
echo Launching default browser to http://127.0.0.1:8000 ...
start http://127.0.0.1:8000
echo Starting FastAPI Uvicorn engine... Press Ctrl+C to stop.
echo.
"!PYTHON_CMD!" -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --reload
pause
