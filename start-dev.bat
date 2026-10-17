@echo off
chcp 65001 >nul
setlocal EnableExtensions
title InvestBuddy Local Platform

REM ============================================================
REM  InvestBuddy - dev mode one-click launcher (backend :8000 + frontend :5173)
REM
REM  PORTABILITY (rewritten 2026-10-09)
REM    This script contains NO absolute local paths. Everything is derived
REM    from %~dp0 (the folder this file lives in), so the project can be
REM    cloned / moved anywhere and still run.
REM    The old version hardcoded:
REM      cd /d D:\Project\InfoData\backend
REM      C:\Users\cht\.workbuddy\binaries\python\versions\3.13.12\python.exe
REM    which broke on any other machine.
REM
REM  Python interpreter resolution order (first hit wins):
REM    1) environment variable INFO_DATA_PYTHON
REM    2) .python-path file next to this script (machine-private, gitignored,
REM       single line containing the full path to the interpreter)
REM    3) project venv  backend\.venv\Scripts\python.exe
REM    4) python found on PATH
REM    If none is found it prints instructions and exits - no silent failure.
REM
REM  NOTE: this file is intentionally ASCII-only. Chinese comments in a .bat
REM  file get mangled by cmd.exe's OEM codepage parsing and can corrupt the
REM  whole script. Keep .bat files ASCII.
REM ============================================================

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"

if not exist "%BACKEND%\app\main.py" (
  echo [ERROR] app\main.py not found under "%BACKEND%"
  echo         Make sure this script sits in the project root and backend\ is intact.
  exit /b 1
)

REM ---------------- resolve Python interpreter ----------------
set "PY="

if defined INFO_DATA_PYTHON (
  if exist "%INFO_DATA_PYTHON%" (
    set "PY=%INFO_DATA_PYTHON%"
  ) else (
    echo [WARN] INFO_DATA_PYTHON points to a missing path, ignored: %INFO_DATA_PYTHON%
  )
)

if not defined PY (
  if exist "%ROOT%.python-path" set /p PY=<"%ROOT%.python-path"
)

if not defined PY (
  if exist "%BACKEND%\.venv\Scripts\python.exe" set "PY=%BACKEND%\.venv\Scripts\python.exe"
)

if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
)

if not defined PY (
  echo.
  echo [ERROR] No usable Python interpreter found. Pick one fix:
  echo           1^) install Python 3.12+ and add it to PATH
  echo           2^) create a venv:  cd /d "%BACKEND%" ^&^& python -m venv .venv
  echo           3^) set env var INFO_DATA_PYTHON to the full interpreter path
  echo           4^) create a file "%ROOT%\.python-path" containing that full path
  echo.
  exit /b 1
)
echo [OK] Python  = %PY%

REM ---------------- resolve ports (overridable via env) ----------------
REM  Same variable names as frontend/vite.config.ts, so the two never drift.
REM  Default 8000 / 5173 preserves the old behaviour when nothing is set.
set "API_PORT=%INFO_DATA_API_PORT%"
if not defined API_PORT set "API_PORT=8000"
set "WEB_PORT=%INFO_DATA_WEB_PORT%"
if not defined WEB_PORT set "WEB_PORT=5173"
echo [OK] Ports   = backend :%API_PORT%  frontend :%WEB_PORT%

REM ---------------- backend :%API_PORT% ----------------
netstat -ano | findstr ":%API_PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo [SKIP] backend already running on :%API_PORT%
) else (
  echo [START] backend API ...  http://127.0.0.1:%API_PORT%/docs
  start "InvestBuddy-Backend" /D "%BACKEND%" cmd /k ""%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %API_PORT%"
)

REM ---------------- frontend :%WEB_PORT% ----------------
where npm >nul 2>&1
if errorlevel 1 (
  echo [WARN] npm not found on PATH - skipping frontend. Install Node.js 18+ and retry.
) else (
  REM  if errorlevel 1 means "not listening" -> start.
  REM  Do NOT use %errorlevel% inside this block: it is expanded when the block is
  REM  parsed, so it would still hold the 0 from "where npm" above and the frontend
  REM  would be reported as "already running" on every launch and never started.
  netstat -ano | findstr ":%WEB_PORT% " | findstr "LISTENING" >nul 2>&1
  if errorlevel 1 (
    if not exist "%FRONTEND%\node_modules" (
      echo [INFO] first run: installing frontend deps ^(npm install^) ...
      pushd "%FRONTEND%"
      call npm install
      popd
    )
    echo [START] frontend dev server ...  http://127.0.0.1:%WEB_PORT%
    start "InvestBuddy-Frontend" /D "%FRONTEND%" cmd /k "npm run dev"
  ) else (
    echo [SKIP] frontend already running on :%WEB_PORT%
  )
)

echo.
echo All services started. Open http://127.0.0.1:%WEB_PORT%
