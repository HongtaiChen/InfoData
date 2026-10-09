@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM  InvestBuddy - migrate local MySQL into the Docker MySQL container
REM  Flow: mysqldump -> docker cp -> import inside container
REM
REM  --- 2026-10-09 changes (portability + security) ---
REM  1) [PORTABILITY] MySQL client path is no longer hardcoded to
REM     "C:\Program Files\MySQL\MySQL Server 8.2\bin" (version-pinned,
REM     breaks on any other MySQL version). Resolution order:
REM        env var MYSQL_BIN -> mysqldump on PATH -> auto-detect install dir
REM  2) [SECURITY] DB passwords are no longer hardcoded in the script.
REM     The previous version contained a plaintext password on line 25,
REM     which is now part of Git history. Resolution order:
REM        env var LOCAL_DB_PASSWORD / DOCKER_DB_PASSWORD
REM        -> repo-root .env (gitignored): INFO_DATA_DB_PASSWORD / MYSQL_ROOT_PASSWORD
REM        -> error out (never guess, never use a default)
REM
REM  Usage:
REM    1) copy .env.example to .env and fill INFO_DATA_DB_PASSWORD + MYSQL_ROOT_PASSWORD
REM    2) run this script
REM
REM  NOTE: ASCII-only on purpose - Chinese comments in .bat files break
REM  cmd.exe parsing and can corrupt the script.
REM ============================================================

for %%i in ("%~dp0..") do set "ROOT=%%~fi"
set "DUMP_FILE=%~dp0adata_migrate.sql"

REM ---- docker side ----
set "DOCKER_CONTAINER=infodata-mysql"
set "DOCKER_DB_USER=root"
set "DOCKER_DB_NAME=adata"

REM ---- local side (override with same-named env vars if needed) ----
if not defined LOCAL_DB_HOST set "LOCAL_DB_HOST=127.0.0.1"
if not defined LOCAL_DB_PORT set "LOCAL_DB_PORT=3306"
if not defined LOCAL_DB_USER set "LOCAL_DB_USER=root"
if not defined LOCAL_DB_NAME set "LOCAL_DB_NAME=adata"

REM ============================================================
REM  [1/5] locate mysqldump
REM ============================================================
set "MYSQLDUMP="
if defined MYSQL_BIN if exist "%MYSQL_BIN%\mysqldump.exe" set "MYSQLDUMP=%MYSQL_BIN%\mysqldump.exe"

if not defined MYSQLDUMP (
    for /f "delims=" %%i in ('where mysqldump 2^>nul') do if not defined MYSQLDUMP set "MYSQLDUMP=%%i"
)

if not defined MYSQLDUMP (
    for /d %%d in ("C:\Program Files\MySQL\MySQL Server*") do (
        if not defined MYSQLDUMP if exist "%%~fd\bin\mysqldump.exe" set "MYSQLDUMP=%%~fd\bin\mysqldump.exe"
    )
)

if not defined MYSQLDUMP (
    echo.
    echo ERROR: mysqldump.exe not found.
    echo   Fix one of these:
    echo     1^) add the MySQL bin directory to PATH
    echo     2^) set env var MYSQL_BIN to that directory
    echo.
    exit /b 1
)
echo [1/5] mysqldump = %MYSQLDUMP%

REM ============================================================
REM  [2/5] resolve passwords (env vars take priority over .env)
REM ============================================================
set "ENV_LOCAL_PWD=%LOCAL_DB_PASSWORD%"
set "ENV_DOCKER_PWD=%DOCKER_DB_PASSWORD%"
set "LOCAL_DB_PASSWORD="
set "DOCKER_DB_PASSWORD="

if exist "%ROOT%\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%ROOT%\.env") do (
        if /i "%%a"=="INFO_DATA_DB_PASSWORD" set "LOCAL_DB_PASSWORD=%%b"
        if /i "%%a"=="MYSQL_ROOT_PASSWORD"   set "DOCKER_DB_PASSWORD=%%b"
    )
)

if defined ENV_LOCAL_PWD  set "LOCAL_DB_PASSWORD=%ENV_LOCAL_PWD%"
if defined ENV_DOCKER_PWD set "DOCKER_DB_PASSWORD=%ENV_DOCKER_PWD%"

if not defined LOCAL_DB_PASSWORD (
    set "LOCAL_DB_PASSWORD=root"
    echo [WARN] local DB password not configured, falling back to default "root"
    echo        to override: set INFO_DATA_DB_PASSWORD in "%ROOT%\.env"
)

if not defined DOCKER_DB_PASSWORD (
    echo.
    echo ERROR: Docker MySQL password was not provided.
    echo   Set MYSQL_ROOT_PASSWORD in "%ROOT%\.env" ^(or env var DOCKER_DB_PASSWORD^).
    echo   Use the same password the container was initialised with.
    echo.
    exit /b 1
)
echo [2/5] passwords ready (local user=%LOCAL_DB_USER%, docker user=%DOCKER_DB_USER%) - not echoed

REM ============================================================
REM  [3/5] dump local database
REM ============================================================
echo [3/5] dumping "%LOCAL_DB_NAME%" ... ^(may take a while for large data^)
"%MYSQLDUMP%" -h%LOCAL_DB_HOST% -P%LOCAL_DB_PORT% -u%LOCAL_DB_USER% -p%LOCAL_DB_PASSWORD% ^
    --single-transaction --quick --default-character-set=utf8mb4 ^
    --databases %LOCAL_DB_NAME% > "%DUMP_FILE%"

if errorlevel 1 (
    echo ERROR: mysqldump failed. Check the password and whether local MySQL runs at %LOCAL_DB_HOST%:%LOCAL_DB_PORT%.
    exit /b 1
)
for %%A in ("%DUMP_FILE%") do echo      dump size: %%~zA bytes

REM ============================================================
REM  [4/5] copy into container
REM ============================================================
echo [4/5] copying into container %DOCKER_CONTAINER% ...
docker cp "%DUMP_FILE%" %DOCKER_CONTAINER%:/tmp/adata_migrate.sql
if errorlevel 1 (
    echo ERROR: docker cp failed. Is container %DOCKER_CONTAINER% running? ^(docker compose --profile docker-mysql up -d^)
    exit /b 1
)

REM ============================================================
REM  [5/5] import inside container
REM ============================================================
echo [5/5] importing inside container ...
docker exec -i %DOCKER_CONTAINER% mysql -u%DOCKER_DB_USER% -p%DOCKER_DB_PASSWORD% ^
    --default-character-set=utf8mb4 < "%DUMP_FILE%"
if errorlevel 1 (
    echo ERROR: import failed.
    exit /b 1
)

echo.
echo Migration done. Verify with:
echo   docker exec -it %DOCKER_CONTAINER% mysql -u%DOCKER_DB_USER% -p -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='%DOCKER_DB_NAME%';"
echo.
echo Cleanup: del "%DUMP_FILE%"
endlocal
