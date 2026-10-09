@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM  InvestBuddy - Docker MySQL backup (Windows)
REM  Dumps the Docker MySQL container to a file on the host.
REM
REM  Usage:  backup-docker-mysql.bat
REM  Output: scripts/backup/adata_YYYYMMDD_HHMMSS.sql
REM
REM  --- 2026-10-10 changes (security + portability) ---
REM  1) [SECURITY] The container root password is no longer hardcoded in
REM     this file (the old literal is now part of Git history - rotate the
REM     container password if it is still in use). Resolution order:
REM        env DOCKER_DB_PASSWORD -> env MYSQL_ROOT_PASSWORD
REM        -> repo-root .env (MYSQL_ROOT_PASSWORD) -> abort (never guess).
REM     It is handed to the container through MYSQL_PWD, so it does not
REM     appear on any command line.
REM  2) [PORTABILITY] The timestamp no longer parses `date /t` (which is
REM     locale dependent and breaks on zh-CN machines); PowerShell is used.
REM
REM  NOTE: ASCII-only on purpose - Chinese comments in .bat files get read
REM  as GBK by cmd.exe and corrupt the script.
REM ============================================================

for %%i in ("%~dp0..") do set "ROOT=%%~fi"

set "DOCKER_CONTAINER=infodata-mysql"
set "DOCKER_DB_USER=root"
set "DOCKER_DB_NAME=adata"

REM ---- [1/4] resolve the container password (never hardcode, never guess) ----
set "DOCKER_DB_PASSWORD="
if defined DOCKER_DB_PASSWORD_OVERRIDE set "DOCKER_DB_PASSWORD=%DOCKER_DB_PASSWORD_OVERRIDE%"
if not defined DOCKER_DB_PASSWORD if defined MYSQL_ROOT_PASSWORD set "DOCKER_DB_PASSWORD=%MYSQL_ROOT_PASSWORD%"
if not defined DOCKER_DB_PASSWORD if exist "%ROOT%\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%ROOT%\.env") do (
        if /i "%%a"=="MYSQL_ROOT_PASSWORD" set "DOCKER_DB_PASSWORD=%%b"
    )
)
if not defined DOCKER_DB_PASSWORD (
    echo.
    echo ERROR: Docker MySQL password not provided.
    echo   Set MYSQL_ROOT_PASSWORD in "%ROOT%\.env" ^(or env var DOCKER_DB_PASSWORD^).
    echo   Use the same password the container was initialised with.
    echo.
    exit /b 1
)

REM ---- timestamp (locale independent) ----
set "TS="
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss" 2^>nul') do set "TS=%%i"
if not defined TS set "TS=backup"

set "BACKUP_DIR=%~dp0backup"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
set "BACKUP_FILE=%BACKUP_DIR%\%DOCKER_DB_NAME%_%TS%.sql"

echo [1/3] Checking container %DOCKER_CONTAINER% ...
docker ps --filter "name=%DOCKER_CONTAINER%" --format "{{.Names}}" | findstr /C:"%DOCKER_CONTAINER%" >nul
if errorlevel 1 (
    echo ERROR: container not running. Start with: docker compose --profile docker-mysql up -d
    exit /b 1
)

echo [2/3] Dumping database %DOCKER_DB_NAME% ...
set "MYSQL_PWD=%DOCKER_DB_PASSWORD%"
docker exec -e MYSQL_PWD %DOCKER_CONTAINER% mysqldump -u%DOCKER_DB_USER% ^
    --single-transaction --quick --default-character-set=utf8mb4 --routines --triggers ^
    %DOCKER_DB_NAME% > "%BACKUP_FILE%"
if errorlevel 1 (
    echo ERROR: mysqldump inside the container failed.
    exit /b 1
)

echo [3/3] Backup written:
for %%A in ("%BACKUP_FILE%") do echo   %%~fA  ^(%%~zA bytes^)

echo.
echo Restore hint (prompts for the password):
echo   docker exec -i %DOCKER_CONTAINER% mysql -u%DOCKER_DB_USER% -p %DOCKER_DB_NAME% ^< "%BACKUP_FILE%"
echo.
echo Migration to another machine: use scripts\migrate-machine.bat instead.
endlocal
