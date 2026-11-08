#!/usr/bin/env sh
# Internify setup for macOS and Linux: opens the setup page, then starts Internify. Run it with:  ./setup.sh
set -e
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install it from https://docs.docker.com/get-docker/ and run this again."; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but not running. Start Docker Desktop, wait until it says it is running, then run this again."; exit 1
fi

[ -f .env ] || : > .env      # Docker needs the file to exist; the setup page fills it in

URL="http://localhost:8765"
( sleep 6; if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"; fi ) >/dev/null 2>&1 &

echo "Opening the setup page at $URL (if your browser does not open, open that address yourself)."
if ! docker compose --profile setup run --rm --service-ports setup; then
  echo; echo "Setup was not finished, so Internify was not started. Run ./setup.sh again when you are ready."; exit 1
fi

docker compose up -d --build
echo
echo "Internify is running. Open Telegram, find your bot and send /start."
echo "Optional live dashboard: http://localhost:8080/live"
echo "Keep Docker running: Internify is only awake while Docker is running."
