@echo off
:: ─── Trading Journal — launch script ───────────────────────────────
:: Reads BACKEND_PORT from backend\.env if present, otherwise defaults to 8001.
:: Uses the python on PATH unless the PYTHON env var overrides it.

setlocal enabledelayedexpansion
title Trading Journal
echo Starting Trading Journal...
echo.

set "PROJECT_DIR=%~dp0"

:: ── Pick python: PYTHON env var > python in PATH ─────────────────
if "%PYTHON%"=="" set "PYTHON=python"

:: ── Read BACKEND_PORT from .env (default 8001) ──────────────────
set "BACKEND_PORT=8001"
if exist "%PROJECT_DIR%backend\.env" (
    for /f "tokens=1,2 delims==" %%a in (%PROJECT_DIR%backend\.env) do (
        if /i "%%a"=="BACKEND_PORT" set "BACKEND_PORT=%%b"
    )
)

:: ── Start backend ────────────────────────────────────────────────
echo [1/2] Starting backend on port %BACKEND_PORT%...
start "TJ-Backend" cmd /k "cd /d %PROJECT_DIR%backend && %PYTHON% -m uvicorn main:app --host 0.0.0.0 --port %BACKEND_PORT%"

timeout /t 3 /nobreak >nul

:: ── Start frontend ───────────────────────────────────────────────
echo [2/2] Starting frontend on port 5173...
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
