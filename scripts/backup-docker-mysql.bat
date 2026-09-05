@echo off
rem ============================================================
rem  InvestBuddy - Docker MySQL Backup (Windows)
rem  备份 Docker MySQL 容器数据到宿主机文件
rem
rem  Usage: backup-docker-mysql.bat
rem  Output: scripts/backup\adata_YYYYMMDD_HHMMSS.sql
rem ============================================================
setlocal

set DOCKER_CONTAINER=infodata-mysql
set DOCKER_DB_USER=root
set DOCKER_DB_PASSWORD=root123456
set DOCKER_DB_NAME=adata

rem timestamp (cmd native, no wmic)
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do set YMD=%%c%%a%%b
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set HM=%%a%%b
if "%YMD%"=="" set YMD=%date:~0,4%%date:~5,2%%date:~8,2%
if "%HM%"=="" set HM=%time:~0,2%%time:~3,2%
set TS=%YMD%_%HM%

set BACKUP_DIR=%~dp0backup
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
set BACKUP_FILE=%BACKUP_DIR%\%DOCKER_DB_NAME%_%TS%.sql

echo [1/3] Checking container %DOCKER_CONTAINER%...
docker ps --filter "name=%DOCKER_CONTAINER%" --format "{{.Names}}" | findstr /C:"%DOCKER_CONTAINER%" >nul
if errorlevel 1 (
    echo ERROR: container not running. Start with: docker compose --profile docker-mysql up -d
    exit /b 1
)

echo [2/3] Dumping database %DOCKER_DB_NAME%...
docker exec %DOCKER_CONTAINER% mysqldump -u%DOCKER_DB_USER% -p%DOCKER_DB_PASSWORD% --single-transaction --quick --default-character-set=utf8mb4 --routines --triggers %DOCKER_DB_NAME% > "%BACKUP_FILE%"
if errorlevel 1 (
    echo ERROR: mysqldump in container failed.
    exit /b 1
)

echo [3/3] Backup written:
for %%A in ("%BACKUP_FILE%") do echo   %%~fA  (%%~zA bytes)

echo.
echo Restore hint: docker exec -i %DOCKER_CONTAINER% mysql -u%DOCKER_DB_USER% -p%DOCKER_DB_PASSWORD% %DOCKER_DB_NAME% < "%BACKUP_FILE%"
endlocal
