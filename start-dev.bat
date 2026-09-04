@echo off
chcp 65001 >nul
title InfoData Local Platform
echo ============================================
echo   InfoData - Local A-Share Data Platform
echo   Backend : http://127.0.0.1:8000  (/docs)
echo   Frontend: http://127.0.0.1:5173
echo ============================================
echo.

REM ---- check ports already in use ----
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo [SKIP] Backend already running on :8000
) else (
  echo [START] Backend API ...
  start "InfoData-Backend" cmd /k "cd /d D:\Project\InfoData\backend && C:\Users\cht\.workbuddy\binaries\python\versions\3.13.12\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
)

netstat -ano | findstr ":5173 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo [SKIP] Frontend already running on :5173
) else (
  echo [START] Frontend dev server ...
  start "InfoData-Frontend" cmd /k "cd /d D:\Project\InfoData\frontend && npm run dev"
)

echo.
echo All services started. Open http://127.0.0.1:5173 in your browser.
timeout /t 2 >nul
