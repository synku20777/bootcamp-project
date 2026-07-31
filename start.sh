#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
    echo "START COULD NOT CONTINUE: Docker is not installed or is not on PATH."
    echo "Install Docker, reopen the terminal, and run ./start.sh again."
    exit 1
fi
if [[ ! -f .env ]]; then
    echo "START COULD NOT CONTINUE: first-time setup has not created .env."
    echo "Run ./setup.sh first."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "START COULD NOT CONTINUE: the Docker engine is not running or is inaccessible."
    echo "Start Docker, verify with 'docker info', and retry."
    exit 1
fi
if ! docker compose up -d --wait --wait-timeout 180; then
    echo "START COULD NOT CONTINUE: one or more services did not become healthy."
    echo "Run 'docker compose ps' and 'docker compose logs <service>'."
    exit 1
fi

echo
echo "SERVICES STARTED SUCCESSFULLY"
echo "Dashboard: http://localhost:8050/overview"
echo "API documentation: http://localhost:8000/docs"
echo "Snowflake was not queried by this start command."
echo "Stop safely with: ./stop.sh"
