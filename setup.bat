@echo off
rem Internify setup for Windows: opens the setup page, then starts Internify. Double-click this file.
cd /d "%~dp0"

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker is not installed. Install Docker Desktop from https://docs.docker.com/get-docker/ and run this again.
  pause & exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker is installed but not running. Start Docker Desktop, wait until it says it is running, then run this again.
  pause & exit /b 1
)

if not exist .env type nul > .env

echo Opening the setup page at http://localhost:8765 (if your browser does not open, open that address yourself).
start "" cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:8765"
docker compose --profile setup run --rm --service-ports setup
if errorlevel 1 (
  echo.
  echo Setup was not finished, so Internify was not started. Run setup.bat again when you are ready.
  pause & exit /b 1
)

docker compose up -d --build
echo.
echo Internify is running. Open Telegram, find your bot and send /start.
echo Optional live dashboard: http://localhost:8080/live
echo Keep Docker running: Internify is only awake while Docker is running.
pause
