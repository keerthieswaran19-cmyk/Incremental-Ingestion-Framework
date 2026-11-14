@echo off
setlocal enabledelayedexpansion
title Incremental Ingestion Framework - Operations Launcher

:: Detect Python executable
set "PYTHON_CMD="

:: 1. Check direct python command
python --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_CMD=python"
    goto :PYTHON_FOUND
)

:: 2. Check py launcher
py -3.11 --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_CMD=py -3.11"
    goto :PYTHON_FOUND
)

:: 3. Check specific Python 3.11 install directory
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto :PYTHON_FOUND
)

:: 4. Check Program Files
if exist "%ProgramFiles%\Python311\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python311\python.exe"
    goto :PYTHON_FOUND
)

:PYTHON_NOT_FOUND
color 0C
echo ==============================================================================
echo [ERROR] Python 3.11+ was not found on your system PATH!
echo Please install Python 3.11 using:
echo   winget install --id Python.Python.3.11 -e --source winget
echo ==============================================================================
pause
exit /b 1

:PYTHON_FOUND
color 0B
cls
echo ==============================================================================
echo        INCREMENTAL DATA INGESTION FRAMEWORK - OPERATIONS LAUNCHER
echo ==============================================================================
echo  Python Detected: !PYTHON_CMD!
echo  Database:        Embedded SQLite WAL Mode (Zero external DB installation)
echo  Architecture:    Bi-Temporal SCD-4 + DLQ Quarantine + Watermark Monotonicity
echo ==============================================================================
echo.
echo Select an option:
echo.
echo   [1] Start REST API ^& Operations Cockpit UI (Opens in Browser)
echo   [2] Run Automated Test Suite (17 pytest scenarios)
echo   [3] Run 100,000+ Records Benchmark ^& Generate Audit Reports
echo   [4] Initialize / Reset SQLite Database (Clean State)
echo   [5] Generate Synthetic Sample Data (100 dirty records)
echo   [6] Full End-to-End Automated Demonstration (Init + Test + Bench + UI)
echo   [7] Exit
echo.
echo ==============================================================================
set /p choice="Enter your choice [1-7]: "

if "%choice%"=="1" goto :START_UI
if "%choice%"=="2" goto :RUN_TESTS
if "%choice%"=="3" goto :RUN_BENCHMARK
if "%choice%"=="4" goto :INIT_DB
if "%choice%"=="5" goto :GEN_DATA
if "%choice%"=="6" goto :RUN_ALL
if "%choice%"=="7" goto :EXIT

echo [ERROR] Invalid choice. Please select 1 through 7.
timeout /t 2 >nul
goto :PYTHON_FOUND

:START_UI
cls
echo ==============================================================================
echo Starting FastAPI Service & Operations Cockpit UI...
echo Endpoint: http://127.0.0.1:8000
echo Swagger:  http://127.0.0.1:8000/docs
echo ==============================================================================
start http://127.0.0.1:8000
"!PYTHON_CMD!" -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --reload
pause
goto :PYTHON_FOUND

:RUN_TESTS
cls
echo ==============================================================================
echo Running 17 Automated Pytest Scenarios...
echo ==============================================================================
"!PYTHON_CMD!" -m pytest tests/ -v
echo.
pause
goto :PYTHON_FOUND

:RUN_BENCHMARK
cls
echo ==============================================================================
echo Running 100,000+ Record Benchmark...
echo ==============================================================================
"!PYTHON_CMD!" scripts/benchmark.py --count 100000 --seed 42
echo.
pause
goto :PYTHON_FOUND

:INIT_DB
cls
echo ==============================================================================
echo Initializing SQLite Database (WAL Mode)...
echo ==============================================================================
"!PYTHON_CMD!" scripts/initialize_db.py --reset
echo.
pause
goto :PYTHON_FOUND

:GEN_DATA
cls
echo ==============================================================================
echo Generating Seeded Synthetic Data (100 records)...
echo ==============================================================================
"!PYTHON_CMD!" scripts/generate_data.py --count 100 --output data/source/sample_data.json
echo.
pause
goto :PYTHON_FOUND

:RUN_ALL
cls
echo ==============================================================================
echo STEP 1/3: Initializing Database...
echo ==============================================================================
"!PYTHON_CMD!" scripts/initialize_db.py --reset
echo.
echo ==============================================================================
echo STEP 2/3: Executing Test Suite...
echo ==============================================================================
"!PYTHON_CMD!" -m pytest tests/ -v
echo.
echo ==============================================================================
echo STEP 3/3: Running 100,000 Record Benchmark...
echo ==============================================================================
"!PYTHON_CMD!" scripts/benchmark.py --count 100000 --seed 42
echo.
echo ==============================================================================
echo All steps completed successfully! Launching UI...
echo ==============================================================================
start http://127.0.0.1:8000
"!PYTHON_CMD!" -m uvicorn api.app:app --host 127.0.0.1 --port 8000
goto :PYTHON_FOUND

:EXIT
exit /b 0
