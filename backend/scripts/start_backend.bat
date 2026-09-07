@echo off
rem InvestBuddy backend starter (autostart / manual double-click)
rem - skips if port 8000 already listening
rem - logs to backend\logs\uvicorn.log
cd /d D:\Project\InfoData\backend
if not exist logs mkdir logs
set "PY=C:\Users\cht\.workbuddy\binaries\python\versions\3.13.12\python.exe"
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [start_backend] port 8000 already in use, skip
  exit /b 0
)
start "InvestBuddy-backend" /min "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >> logs\uvicorn.log 2>&1
echo [start_backend] backend starting, see logs\uvicorn.log
