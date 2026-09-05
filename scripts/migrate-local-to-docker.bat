@echo off
rem ============================================================
rem  InvestBuddy - Local MySQL to Docker MySQL Migration (Windows)
rem  本机 MySQL -> Docker MySQL 容器数据迁移
rem
rem  Prerequisites:
rem   1. docker compose --profile docker-mysql up -d   (mysql container up)
rem   2. Adjust LOCAL_MYSQL_* below to match your local MySQL
rem
rem  Steps: mysqldump to file -> docker cp -> import in container
rem ============================================================
setlocal enabledelayedexpansion

rem ---- local MySQL connection (change if needed) ----
set LOCAL_MYSQL_BIN=C:\Program Files\MySQL\MySQL Server 8.2\bin
set LOCAL_DB_HOST=127.0.0.1
set LOCAL_DB_PORT=3306
set LOCAL_DB_USER=root
set LOCAL_DB_PASSWORD=root
set LOCAL_DB_NAME=adata

rem ---- docker mysql container ----
set DOCKER_CONTAINER=infodata-mysql
set DOCKER_DB_USER=root
set DOCKER_DB_PASSWORD=root123456
set DOCKER_DB_NAME=adata

set DUMP_FILE=%~dp0adata_migrate.sql

echo [1/4] Checking mysqldump...
if not exist "%LOCAL_MYSQL_BIN%\mysqldump.exe" (
    echo ERROR: mysqldump not found at %LOCAL_MYSQL_BIN%
    exit /b 1
)

echo [2/4] Dumping local database "%LOCAL_DB_NAME%" (this may take a while for large data)...
"%LOCAL_MYSQL_BIN%\mysqldump.exe" -h%LOCAL_DB_HOST% -P%LOCAL_DB_PORT% -u%LOCAL_DB_USER% -p%LOCAL_DB_PASSWORD% --single-transaction --quick --default-character-set=utf8mb4 --routines --triggers --databases %LOCAL_DB_NAME% > "%DUMP_FILE%"
if errorlevel 1 (
    echo ERROR: mysqldump failed. Check credentials in this script header.
    exit /b 1
)
echo      Dump size: 
for %%A in ("%DUMP_FILE%") do echo      %%~zA bytes

echo [3/4] Copying dump into container...
docker cp "%DUMP_FILE%" %DOCKER_CONTAINER%:/tmp/adata_migrate.sql
if errorlevel 1 (
    echo ERROR: docker cp failed. Is container %DOCKER_CONTAINER% running?
    exit /b 1
)

echo [4/4] Importing into docker mysql (adata db)...
docker exec -i %DOCKER_CONTAINER% mysql -u%DOCKER_DB_USER% -p%DOCKER_DB_PASSWORD% --default-character-set=utf8mb4 -e "DROP DATABASE IF EXISTS %DOCKER_DB_NAME%; CREATE DATABASE %DOCKER_DB_NAME% CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"
docker exec -i %DOCKER_CONTAINER% mysql -u%DOCKER_DB_USER% -p%DOCKER_DB_PASSWORD% --default-character-set=utf8mb4 < "%DUMP_FILE%"
if errorlevel 1 (
    echo ERROR: import failed.
    exit /b 1
)

echo.
echo Migration done! Verify:
echo   docker exec %DOCKER_CONTAINER% mysql -u%DOCKER_DB_USER% -p%DOCKER_DB_PASSWORD% -e "SELECT COUNT(*) FROM %DOCKER_DB_NAME%.stock_market_daily;"
echo.
echo Cleanup: del "%DUMP_FILE%"
endlocal
