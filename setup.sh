#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

setup_failure() {
    local what_happened="$1"
    local likely_cause="$2"
    local how_to_fix="$3"
    local technical_reference="$4"

    echo
    echo "SETUP COULD NOT CONTINUE"
    echo "=========================="
    echo "What happened: $what_happened"
    echo "Likely cause: $likely_cause"
    echo "How to fix: $how_to_fix"
    echo "Then run: ./setup.sh --resume"
    echo "Technical reference: $technical_reference"
    exit 1
}

if ! command -v docker >/dev/null 2>&1; then
    setup_failure \
        "Docker is not installed or is not on PATH." \
        "Docker Desktop or Docker Engine has not been installed." \
        "Install Docker, reopen the terminal, and verify with 'docker --version'." \
        "https://docs.docker.com/engine/install/"
fi

if ! docker info >/dev/null 2>&1; then
    setup_failure \
        "Docker is installed, but its engine is not responding." \
        "Docker Desktop is still starting, the Docker service is stopped, or the current user cannot access the Docker socket." \
        "Start Docker and verify with 'docker info'. On Linux, configure non-root Docker access if permission is denied." \
        "docker info"
fi

if ! docker compose version >/dev/null 2>&1; then
    setup_failure \
        "The Docker Compose v2 plugin is unavailable." \
        "Docker was installed without Compose v2." \
        "Install the Compose plugin and verify with 'docker compose version'." \
        "docker compose version"
fi

echo
echo "Step 1 of 9 -- Validate Docker and host ports"
echo "Checking Docker plus the API, dashboard, and MongoDB ports before Snowflake setup."

project_ports="$(docker ps \
    --filter "label=com.docker.compose.project=covid-platform" \
    --format "{{.Ports}}")"
for port in 8000 8050 27017; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1; then
        if [[ "$project_ports" != *":$port->"* ]]; then
            setup_failure \
                "Port $port is already owned by another process." \
                "Another local application is using a port required by this project." \
                "Inspect it with 'lsof -i :$port' or 'ss -ltnp', then stop or reconfigure only a process you recognize." \
                "host port $port"
        fi
    fi
done

export SETUP_LAUNCH_COMMAND="./setup.sh"
export SETUP_RESUME_COMMAND="./setup.sh --resume"
export SETUP_START_COMMAND="./start.sh"
export SETUP_STOP_COMMAND="./stop.sh"
export COMPOSE_IGNORE_ORPHANS=true

if ! docker compose -f compose.setup.yaml build setup; then
    setup_failure \
        "The pinned setup image could not be built." \
        "Docker could not download a base image, access the build cache, or complete the locked dependency installation." \
        "Check internet access and free Docker disk space, then retry. No Snowflake setup was started." \
        "docker compose -f compose.setup.yaml build setup"
fi

setup_run_args=(--rm)
if [[ "$(uname -s)" == "Linux" ]]; then
    setup_run_args+=(--user "$(id -u):$(id -g)" -e HOME=/tmp)
fi
if docker compose -f compose.setup.yaml run "${setup_run_args[@]}" setup setup-data "$@"; then
    :
else
    setup_status=$?
    exit "$setup_status"
fi

if ! docker compose config --quiet; then
    setup_failure \
        "The generated runtime configuration is not valid for Docker Compose." \
        "A required value is missing or malformed in the generated .env file." \
        "Use the setup audit reference to correct the reported value, then resume." \
        "docker compose config"
fi

if ! docker compose up --build -d --wait --wait-timeout 180; then
    setup_failure \
        "One or more application containers did not become healthy." \
        "A port conflict, old MongoDB credentials, image build failure, or unhealthy dependency prevented startup." \
        "Run 'docker compose ps' and then 'docker compose logs <service>' for the affected service. Do not delete volumes unless the README explicitly directs you to." \
        "docker compose ps"
fi

if ! docker compose exec -T api python -m scripts.setup_mongodb; then
    setup_failure \
        "MongoDB annotation indexes could not be created." \
        "MongoDB is unavailable or its existing volume uses different credentials." \
        "Check 'docker compose ps mongo' and follow the README's MongoDB credential recovery instructions." \
        "docker compose logs mongo api"
fi

finalize_args=()
if [[ " $* " == *" --resume "* ]]; then
    finalize_args+=(--resume)
fi
docker compose exec -T \
    -e SETUP_LAUNCH_COMMAND=./setup.sh \
    -e "SETUP_RESUME_COMMAND=./setup.sh --resume" \
    -e SETUP_START_COMMAND=./start.sh \
    -e SETUP_STOP_COMMAND=./stop.sh \
    api python -m scripts.bootstrap finalize-container-setup "${finalize_args[@]}"
