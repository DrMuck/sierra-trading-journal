@echo off
:: ─── Trading Journal — first-time setup script ──────────────────────
:: Run this once after cloning/copying the project to a new machine.
:: It will:
::   1. Check Python + Node are installed
::   2. Install Python dependencies (backend/requirements.txt)
::   3. Install Node dependencies (frontend/package.json)
::   4. Create backend/.env from .env.example (you edit it after)
::   5. Create backend/commissions.json from commissions.example.json
::
:: After setup, run start.bat to launch the app.

setlocal enabledelayedexpansion
title Trading Journal — Setup
echo.
echo ============================================================
echo  Trading Journal — Setup
echo ============================================================
echo.

set "PROJECT_DIR=%~dp0"
set "ERR=0"

:: ── 1. Python check ────────────────────────────────────────────────
echo [1/5] Checking Python ...
where python >nul 2>nul
if errorlevel 1 (
    echo   ERROR: python is not in PATH.
    echo   Install Python 3.11+ from https://www.python.org/downloads/
    echo   or set the PYTHON env var to the absolute path of python.exe.
    set "ERR=1"
    goto :end
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo   Found python !PYVER!
echo.

:: ── 2. Node check ─────────────────────────────────────────────────
echo [2/5] Checking Node ...
where node >nul 2>nul
if errorlevel 1 (
    echo   ERROR: node is not in PATH.
    echo   Install Node 20+ from https://nodejs.org/
    set "ERR=1"
    goto :end
)
for /f "tokens=*" %%v in ('node --version 2^>^&1') do set "NODEVER=%%v"
echo   Found node !NODEVER!
echo.

:: ── 3. Python deps ────────────────────────────────────────────────
echo [3/5] Installing Python packages (backend/requirements.txt) ...
pushd "%PROJECT_DIR%backend"
python -m pip install --user -r requirements.txt
if errorlevel 1 (
    echo   ERROR: pip install failed.
    set "ERR=1"
    popd
    goto :end
)
popd
echo.

:: ── 4. Node deps ──────────────────────────────────────────────────
echo [4/5] Installing Node packages (frontend) ...
pushd "%PROJECT_DIR%frontend"
call npm install
if errorlevel 1 (
    echo   ERROR: npm install failed.
    set "ERR=1"
    popd
    goto :end
)
popd
echo.

:: ── 5. Config files ───────────────────────────────────────────────
echo [5/5] Setting up config files ...
if not exist "%PROJECT_DIR%backend\.env" (
    if exist "%PROJECT_DIR%backend\.env.example" (
        copy /Y "%PROJECT_DIR%backend\.env.example" "%PROJECT_DIR%backend\.env" >nul
        echo   Created backend\.env from .env.example  --  EDIT THIS to match your Sierra Chart install path
    )
) else (
    echo   backend\.env already exists, leaving it alone
)

if not exist "%PROJECT_DIR%backend\commissions.json" (
    if exist "%PROJECT_DIR%backend\commissions.example.json" (
        copy /Y "%PROJECT_DIR%backend\commissions.example.json" "%PROJECT_DIR%backend\commissions.json" >nul
        echo   Created backend\commissions.json from example  --  EDIT to set your broker commission rates
    )
) else (
    echo   backend\commissions.json already exists, leaving it alone
)
echo.

:end
if "%ERR%"=="1" (
    echo ============================================================
    echo  SETUP FAILED — fix the error above and re-run setup.bat
    echo ============================================================
) else (
    echo ============================================================
    echo  SETUP COMPLETE
    echo ============================================================
    echo.
    echo  NEXT STEPS
    echo  ----------
    echo  1. Edit  backend\.env             ^( your Sierra Chart paths ^)
    echo  2. Edit  backend\commissions.json ^( your broker fees       ^)
    echo  3. Run   start.bat                ^( launches backend + UI  ^)
    echo.
    echo  README.md has the details.
    echo.
)
pause
endlocal
