@echo off
rem ============================================================
rem  InvestBuddy - Docker one-click start (Windows)
rem
rem  Usage:
rem    docker-start.bat          - Mode A: backend+frontend, use local MySQL
rem    docker-start.bat full     - Mode B: mysql+backend+frontend (all-in-docker)
rem
rem  Prerequisites: Docker Desktop running
rem ============================================================
setlocal

if "%1"=="full" goto MODE_FULL
goto MODE_A

:MODE_A
echo ============================================================
echo  Mode A: backend + frontend (MySQL: local host 3306)
echo ============================================================
docker compose up -d --build
if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check: Docker Desktop running? .env correct?
    pause
    exit /b 1
)
goto DONE

:MODE_FULL
echo ============================================================
echo  Mode B: mysql + backend + frontend (all-in-docker)
echo  First run? Execute scripts\migrate-local-to-docker.bat
echo ============================================================
docker compose --profile docker-mysql up -d --build
if errorlevel 1 (
    echo.
    echo BUILD FAILED. Check: Docker Desktop running? .env correct?
    pause
    exit /b 1
)
goto DONE

:DONE
echo.
echo ============================================================
echo  Frontend : http://127.0.0.1:8080
echo  Backend  : http://127.0.0.1:18000/api/health
if "%1"=="full" echo  MySQL    : 127.0.0.1:3307
echo ============================================================
echo.
echo  Logs: docker compose logs -f backend
pause
endlocal
