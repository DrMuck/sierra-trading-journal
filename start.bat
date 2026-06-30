@echo off
:: ─── Trading Journal — launch script ───────────────────────────────
:: Reads BACKEND_PORT and PYTHON from backend\.env if present.
:: Python resolution order:
::   1. PYTHON env var (shell override)
::   2. PYTHON=... in backend\.env
::   3. `py` (Windows Python launcher) if installed
::   4. `python` in PATH (last resort — Windows ships a stub that lacks pip
::      packages, so this works only if you've installed deps for it)

setlocal enabledelayedexpansion
title Trading Journal
echo Starting Trading Journal...
echo.

set "PROJECT_DIR=%~dp0"

:: ── Defaults ─────────────────────────────────────────────────────
set "BACKEND_PORT=8001"
set "PYTHON_FROM_ENV="

:: ── Read .env for BACKEND_PORT and PYTHON ────────────────────────
if exist "%PROJECT_DIR%backend\.env" (
    for /f "tokens=1,* delims==" %%a in (%PROJECT_DIR%backend\.env) do (
        if /i "%%a"=="BACKEND_PORT" set "BACKEND_PORT=%%b"
        if /i "%%a"=="PYTHON"       set "PYTHON_FROM_ENV=%%b"
    )
)

:: ── Resolve Python ───────────────────────────────────────────────
if not "%PYTHON%"==""           ( set "PYBIN=%PYTHON%"           & goto :pyfound )
if not "%PYTHON_FROM_ENV%"=="" ( set "PYBIN=%PYTHON_FROM_ENV%"  & goto :pyfound )
where py >nul 2>nul     && ( set "PYBIN=py"     & goto :pyfound )
where python >nul 2>nul && ( set "PYBIN=python" & goto :pyfound )

echo ERROR: No usable Python found. Either:
echo   1. Install Python 3.11+ from https://python.org, OR
echo   2. Set PYTHON=^<path-to-python.exe^> in backend\.env, OR
echo   3. Set PYTHON env var in this shell before running start.bat
pause
exit /b 1

:pyfound
echo Using Python: %PYBIN%

:: ── Start backend ────────────────────────────────────────────────
echo [1/2] Starting backend on port %BACKEND_PORT% ...
start "TJ-Backend" cmd /k "cd /d %PROJECT_DIR%backend && %PYBIN% -m uvicorn main:app --host 0.0.0.0 --port %BACKEND_PORT%"

timeout /t 3 /nobreak >nul

:: ── Start frontend ───────────────────────────────────────────────
echo [2/2] Starting frontend on port 5173 ...
start "TJ-Frontend" cmd /k "cd /d %PROJECT_DIR%frontend && npm run dev"

timeout /t 3 /nobreak >nul
echo.
echo Opening browser...
start http://localhost:5173

echo.
echo Trading Journal is running!
echo   Backend:  http://localhost:%BACKEND_PORT%
echo   Frontend: http://localhost:5173
echo.
echo Close the two terminal windows to stop.
endlocal
