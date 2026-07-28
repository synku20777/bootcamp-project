#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
    echo "STOP COULD NOT CONTINUE: Docker is not installed or is not on PATH."
    exit 1
fi
if ! docker compose stop; then
    echo "STOP COULD NOT CONTINUE: Docker could not stop the Compose services."
    echo "Start Docker, inspect 'docker compose ps', and retry."
    exit 1
fi

echo
echo "SERVICES STOPPED SAFELY"
echo "MongoDB and Redis volumes were preserved; Snowflake objects were unchanged."
echo "Start again with: ./start.sh"
