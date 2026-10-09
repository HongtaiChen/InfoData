@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM  InvestBuddy - whole-database migration between machines
REM
REM  Usage:
REM    migrate-machine.bat dump [out.sql]     run on the SOURCE machine
REM    migrate-machine.bat load [in.sql] [-y] run on the TARGET machine
REM
REM  Why migrate the database instead of re-collecting data:
REM    * stock_market_daily_ex (16M rows / 3.2GB) is a FROZEN archive
REM      table with NO collector (see app/selfheal.py) - re-collecting
REM      can never bring it back.
REM    * news / finance_calendar / ths_concept_market only expose short
REM      upstream windows, so their history is not recoverable either.
REM    * The dump already carries CREATE DATABASE + all 56 tables + the
REM      task_config / dq_rules / table_meta configuration, so after a
REM      migration you do NOT need schema.sql nor the seed_* scripts.
REM
REM  Password handling: read from env INFO_DATA_DB_PASSWORD, else from
REM  the repo-root .env, else fall back to "root" (with a warning).
REM  It is passed through the MYSQL_PWD env var, so the plaintext never
REM  appears in the process command line / console history.
REM
REM  Extra args: set MIGRATE_EXTRA_ARGS to append raw mysqldump options,
REM  e.g. MIGRATE_EXTRA_ARGS=--no-data for a structure-only export.
REM
REM  NOTE: ASCII-only on purpose - Chinese comments in .bat files get
REM  read as GBK by cmd.exe and corrupt the script.
REM ============================================================

REM ---- repo root + connection defaults (override via env) ----
for %%i in ("%~dp0..") do set "ROOT=%%~fi"
if not defined INFO_DATA_DB_HOST set "INFO_DATA_DB_HOST=127.0.0.1"
if not defined INFO_DATA_DB_PORT set "INFO_DATA_DB_PORT=3306"
if not defined INFO_DATA_DB_USER set "INFO_DATA_DB_USER=root"
if not defined INFO_DATA_DB_NAME set "INFO_DATA_DB_NAME=adata"
set "DB=%INFO_DATA_DB_NAME%"
set "BACKUP_DIR=%ROOT%\scripts\backup"

set "MODE=%~1"
if /i "%MODE%"=="dump" goto :dump
if /i "%MODE%"=="load" goto :load
call :usage
exit /b 1

REM ============================================================
REM  DUMP (source machine)
REM ============================================================
:dump
call :locate
call :resolve_pwd

set "OUT=%~2"
if "%OUT%"=="" (
    if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
    set "TS="
    for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss" 2^>nul') do set "TS=%%i"
    if not defined TS set "TS=migrate"
    set "OUT=%BACKUP_DIR%\adata_migrate_!TS!.sql"
)

echo [1/4] mysqldump = %MYSQLDUMP%
echo [2/4] source    = %DB% @ %INFO_DATA_DB_HOST%:%INFO_DATA_DB_PORT%
echo.

REM ---- discover archive/backup tables to skip (saves ~1.6GB) ----
REM The list is queried into a temp file first. Running a quoted exe path
REM directly inside for /f does not work here: cmd.exe strips the outer
REM quotes whenever the command starts with a quote, so the path with
REM spaces ("C:\Program Files\...") breaks.
set "TABLELIST=%TEMP%\infodata_archives_%RANDOM%.txt"
"%MYSQL%" --host=%INFO_DATA_DB_HOST% --port=%INFO_DATA_DB_PORT% --user=%INFO_DATA_DB_USER% -N -B -e "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='%DB%' AND (TABLE_NAME REGEXP '_bak_[0-9]{8}$' OR TABLE_NAME REGEXP '_backup_[0-9]{8}$')" > "%TABLELIST%" 2>nul
set "IGNORE="
for /f "usebackq delims=" %%t in ("%TABLELIST%") do set "IGNORE=!IGNORE! --ignore-table=%DB%.%%t"
del "%TABLELIST%" 2>nul
if defined IGNORE (
    echo [3/4] skip      =!IGNORE!
) else (
    echo [3/4] WARN: no archive table detected - the dump will include everything
)

echo [4/4] writing   = %OUT%
echo        ^(large database: expect several minutes and several GB^)
echo.
"%MYSQLDUMP%" --host=%INFO_DATA_DB_HOST% --port=%INFO_DATA_DB_PORT% --user=%INFO_DATA_DB_USER% ^
    --single-transaction --quick --default-character-set=utf8mb4 ^
    --routines --triggers --events ^
    --databases %DB% !IGNORE! !MIGRATE_EXTRA_ARGS! > "%OUT%"

if errorlevel 1 (
    echo.
    echo ERROR: mysqldump failed. Check the password and that MySQL runs at %INFO_DATA_DB_HOST%:%INFO_DATA_DB_PORT%.
    del "%OUT%" 2>nul
    exit /b 1
)

echo.
for %%A in ("%OUT%") do echo Done. dump size = %%~zA bytes
echo.
echo Next: copy this file to the target machine, then run
echo        migrate-machine.bat load "path\to\the-dump.sql"
echo      ^(or keep the default file name and just run "migrate-machine.bat load"^)
echo    Do NOT commit this file to git - it is several GB.
exit /b 0

REM ============================================================
REM  LOAD (target machine)
REM ============================================================
:load
call :locate
call :resolve_pwd

set "IN=%~2"
if "%IN%"=="" set "IN=%BACKUP_DIR%\adata_migrate.sql"
if not exist "%IN%" (
    echo ERROR: dump file not found: "%IN%"
    echo   pass the path explicitly:  migrate-machine.bat load "D:\path\to\data.sql"
    exit /b 1
)

echo Target DB  = %DB% @ %INFO_DATA_DB_HOST%:%INFO_DATA_DB_PORT%
echo Source file = %IN%
echo.
echo WARNING: the dump contains DROP TABLE statements. Any table that
echo          already exists in database "%DB%" WILL BE DROPPED and recreated.
echo.
if /i "%~3"=="-y" goto :load_go
if defined MIGRATE_YES goto :load_go
set /p CONFIRM=Type YES to continue: 
if /i not "%CONFIRM%"=="YES" (
    echo Aborted - nothing was imported.
    exit /b 1
)

:load_go
echo [1/2] raising max_allowed_packet ^(best effort^) ...
"%MYSQL%" --host=%INFO_DATA_DB_HOST% --port=%INFO_DATA_DB_PORT% --user=%INFO_DATA_DB_USER% ^
    -e "SET GLOBAL max_allowed_packet=268435456;" 2>nul

echo [2/2] importing ... ^(expect 10-30 minutes^)
"%MYSQL%" --host=%INFO_DATA_DB_HOST% --port=%INFO_DATA_DB_PORT% --user=%INFO_DATA_DB_USER% ^
    --default-character-set=utf8mb4 --max-allowed-packet=268435456 < "%IN%"
if errorlevel 1 (
    echo.
    echo ERROR: import failed. Common causes:
    echo   - target MySQL older than 8.0 ^(cannot handle utf8mb4_0900_ai_ci^)
    echo   - not enough disk space
    echo   - wrong password in INFO_DATA_DB_PASSWORD
    exit /b 1
)

echo.
echo Done. Next steps on this machine:
echo   1^) point the backend at this MySQL ^(dev mode reads ENV, not .env^):
echo        setx INFO_DATA_DB_PASSWORD "your-mysql-password"
echo        ^(plus INFO_DATA_DB_HOST / _PORT / _USER / _NAME if not the defaults^)
echo   2^) start the app:  start-dev.bat
echo.
echo Note: schema.sql and the seed_* scripts are NOT needed here - the dump
echo       already contains task_config / dq_rules / table_meta.
exit /b 0

REM ============================================================
REM  helpers
REM ============================================================
:locate
set "MYSQLDUMP="
set "MYSQL="
if defined MYSQL_BIN (
    if exist "%MYSQL_BIN%\mysqldump.exe" set "MYSQLDUMP=%MYSQL_BIN%\mysqldump.exe"
    if exist "%MYSQL_BIN%\mysql.exe"     set "MYSQL=%MYSQL_BIN%\mysql.exe"
)
if not defined MYSQLDUMP for /f "delims=" %%i in ('where mysqldump 2^>nul') do if not defined MYSQLDUMP set "MYSQLDUMP=%%i"
if not defined MYSQL     for /f "delims=" %%i in ('where mysql 2^>nul')     do if not defined MYSQL     set "MYSQL=%%i"
if not defined MYSQLDUMP for /d %%d in ("C:\Program Files\MySQL\MySQL Server*") do if not defined MYSQLDUMP if exist "%%~fd\bin\mysqldump.exe" set "MYSQLDUMP=%%~fd\bin\mysqldump.exe"
if not defined MYSQL     for /d %%d in ("C:\Program Files\MySQL\MySQL Server*") do if not defined MYSQL     if exist "%%~fd\bin\mysql.exe"     set "MYSQL=%%~fd\bin\mysql.exe"
if not defined MYSQLDUMP (
    echo ERROR: mysqldump.exe not found.
    echo   Fix one of these:
    echo     1^) set env var MYSQL_BIN to the MySQL bin directory
    echo     2^) add the MySQL bin directory to PATH
    exit /b 1
)
if not defined MYSQL (
    echo ERROR: mysql.exe not found ^(needed to list archive tables^).
    echo     1^) set env var MYSQL_BIN to the MySQL bin directory
    echo     2^) add the MySQL bin directory to PATH
    exit /b 1
)
exit /b 0

:resolve_pwd
set "DB_PWD="
if defined INFO_DATA_DB_PASSWORD set "DB_PWD=%INFO_DATA_DB_PASSWORD%"
if not defined DB_PWD if exist "%ROOT%\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%ROOT%\.env") do (
        if /i "%%a"=="INFO_DATA_DB_PASSWORD" set "DB_PWD=%%b"
    )
)
if not defined DB_PWD (
    set "DB_PWD=root"
    echo [WARN] DB password not configured - falling back to "root".
    echo        set INFO_DATA_DB_PASSWORD, or add it to "%ROOT%\.env"
)
set "MYSQL_PWD=%DB_PWD%"
exit /b 0

:usage
echo.
echo InvestBuddy - whole-database migration between machines
echo.
echo   migrate-machine.bat dump [out.sql]      export on the SOURCE machine
echo   migrate-machine.bat load [in.sql] [-y]  import on the TARGET machine
echo.
echo   dump default output: scripts\backup\adata_migrate_YYYYMMDD_HHMMSS.sql
echo   load default input : scripts\backup\adata_migrate.sql
echo.
echo   password: env INFO_DATA_DB_PASSWORD, else repo-root .env, else "root"
echo   structure-only export: set MIGRATE_EXTRA_ARGS=--no-data
echo.
exit /b 0
