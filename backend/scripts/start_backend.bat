@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  InvestBuddy backend launcher (autostart at logon / manual double-click)
REM
REM  PORTABILITY (rewritten 2026-10-09)
REM    Uses %~dp0 only - NO absolute local paths.
REM    The old version hardcoded:
REM      cd /d D:\Project\InfoData\backend
REM      set "PY=C:\Users\cht\.workbuddy\binaries\python\versions\3.13.12\python.exe"
REM    so it failed on any other machine / clone location.
REM
REM  Python resolution order (first hit wins):
REM    1) environment variable INFO_DATA_PYTHON
REM    2) repo-root .python-path file (machine-private, gitignored, one line)
REM    3) backend\.venv\Scripts\python.exe
REM    4) python found on PATH
REM
REM  Behaviour: skip if port 8000 is already listening; otherwise start
REM             minimized and append logs to backend\logs\uvicorn.log
REM
REM  NOTE: ASCII-only on purpose - Chinese comments inside .bat break
REM  cmd.exe parsing and can corrupt the script.
REM ============================================================

REM ---- self locate (for /f gives a normalized absolute path) ----
for %%i in ("%~dp0..")    do set "BACKEND=%%~fi"
for %%i in ("%~dp0..\..") do set "ROOT=%%~fi"
set "LOG=%BACKEND%\logs\uvicorn.log"

if not exist "%BACKEND%\app\main.py" (
  echo [start_backend] ERROR: "%BACKEND%\app\main.py" not found
  exit /b 1
)

REM ---------------- resolve Python interpreter ----------------
set "PY="

if defined INFO_DATA_PYTHON (
  if exist "%INFO_DATA_PYTHON%" (set "PY=%INFO_DATA_PYTHON%")
)

if not defined PY (
  if exist "%ROOT%\.python-path" set /p PY=<"%ROOT%\.python-path"
)

if not defined PY (
  if exist "%BACKEND%\.venv\Scripts\python.exe" set "PY=%BACKEND%\.venv\Scripts\python.exe"
)

if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
)

if not defined PY (
  echo [start_backend] ERROR: no usable Python interpreter found.
  echo   Set INFO_DATA_PYTHON / create "%ROOT%\.python-path" / create backend\.venv / put python on PATH
  exit /b 1
)

REM ---------------- port check ----------------
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [start_backend] port 8000 already in use, skip
  exit /b 0
)

if not exist "%BACKEND%\logs" mkdir "%BACKEND%\logs"

start "InvestBuddy-backend" /min /D "%BACKEND%" cmd /c ""%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >> "%LOG%" 2>&1"
echo [start_backend] backend starting, see "%LOG%"
