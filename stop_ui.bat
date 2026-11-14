@echo off
setlocal enabledelayedexpansion
title IngestOps - Stop UI Server

color 0E
echo ==============================================================================
echo                 INGESTOPS - STOP OPERATIONS COCKPIT SERVER
echo ==============================================================================
echo.
echo Searching for active processes listening on port 8000...

set "FOUND=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING 2^>nul') do (
    set "PID=%%a"
    if defined PID (
        set "FOUND=1"
        echo Found active server process with PID: !PID!
        echo Terminating process !PID!...
        taskkill /F /PID !PID! >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            echo Process !PID! stopped successfully.
        ) else (
            echo Could not stop process !PID!. It may have already exited.
        )
    )
)

echo.
if "!FOUND!"=="0" (
    color 0A
    echo ==============================================================================
    echo [INFO] No server was listening on port 8000. Port is completely free!
    echo ==============================================================================
) else (
    color 0A
    echo ==============================================================================
    echo [SUCCESS] IngestOps server has been stopped. Port 8000 is now free.
    echo You can now run 'start_ui.bat' or 'run.bat' whenever you want.
    echo ==============================================================================
)

echo.
pause
