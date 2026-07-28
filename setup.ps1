$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}
Set-Location $PSScriptRoot

function Stop-Setup {
    param(
        [string]$WhatHappened,
        [string]$LikelyCause,
        [string]$HowToFix,
        [string]$TechnicalReference
    )

    Write-Host ""
    Write-Host "SETUP COULD NOT CONTINUE"
    Write-Host "=========================="
    Write-Host "What happened: $WhatHappened"
    Write-Host "Likely cause: $LikelyCause"
    Write-Host "How to fix: $HowToFix"
    Write-Host "Then run: .\setup.ps1 --resume"
    Write-Host "Technical reference: $TechnicalReference"
    exit 1
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Setup `
        "Docker is not installed or is not on PATH." `
        "Docker Desktop has not been installed, or this terminal predates the installation." `
        "Install Docker Desktop, reopen PowerShell, and verify with 'docker --version'." `
        "https://docs.docker.com/desktop/"
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-Setup `
        "Docker is installed, but its engine is not responding." `
        "Docker Desktop is still starting or its engine is stopped." `
        "Start Docker Desktop, wait until it reports that Docker is running, and verify with 'docker info'." `
        "docker info"
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-Setup `
        "The Docker Compose v2 plugin is unavailable." `
        "Docker was installed without Compose v2." `
        "Install or update Docker Desktop and verify with 'docker compose version'." `
        "docker compose version"
}

Write-Host ""
Write-Host "Step 1 of 9 -- Validate Docker and host ports"
Write-Host "Checking Docker plus the API, dashboard, and MongoDB ports before Snowflake setup."

$projectPorts = docker ps `
    --filter "label=com.docker.compose.project=covid-platform" `
    --format "{{.Ports}}"
foreach ($port in @(8000, 8050, 27017)) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listeners -and ($projectPorts -notmatch ":$port->")) {
        $owners = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
        Stop-Setup `
            "Port $port is already owned by another process (PID: $owners)." `
            "Another local application is using a port required by this project." `
            "Inspect it with 'Get-Process -Id <PID>' and stop or reconfigure only a process you recognize." `
            "Get-NetTCPConnection -LocalPort $port"
    }
}

$env:SETUP_LAUNCH_COMMAND = ".\setup.ps1"
$env:SETUP_RESUME_COMMAND = ".\setup.ps1 --resume"
$env:SETUP_START_COMMAND = ".\start.ps1"
$env:SETUP_STOP_COMMAND = ".\stop.ps1"
$env:COMPOSE_IGNORE_ORPHANS = "true"

docker compose -f compose.setup.yaml build setup
if ($LASTEXITCODE -ne 0) {
    Stop-Setup `
        "The pinned setup image could not be built." `
        "Docker could not download a base image, access the build cache, or complete the locked dependency installation." `
        "Check internet access and free Docker disk space, then retry. No Snowflake setup was started." `
        "docker compose -f compose.setup.yaml build setup"
}

docker compose -f compose.setup.yaml run --rm setup setup-data @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

docker compose config --quiet
if ($LASTEXITCODE -ne 0) {
    Stop-Setup `
        "The generated runtime configuration is not valid for Docker Compose." `
        "A required value is missing or malformed in the generated .env file." `
        "Use the setup audit reference to correct the reported value, then resume." `
        "docker compose config"
}

docker compose up --build -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) {
    Stop-Setup `
        "One or more application containers did not become healthy." `
        "A port conflict, old MongoDB credentials, image build failure, or unhealthy dependency prevented startup." `
        "Run 'docker compose ps'. For the affected service, run 'docker compose logs <service>'. Do not delete volumes unless the README explicitly directs you to." `
        "docker compose ps"
}

docker compose exec -T api python -m scripts.setup_mongodb
if ($LASTEXITCODE -ne 0) {
    Stop-Setup `
        "MongoDB annotation indexes could not be created." `
        "MongoDB is unavailable or its existing volume uses different credentials." `
        "Check 'docker compose ps mongo' and follow the README's MongoDB credential recovery instructions." `
        "docker compose logs mongo api"
}

$finalizeArgs = @()
if ($args -contains "--resume") {
    $finalizeArgs += "--resume"
}
docker compose exec -T `
    -e "SETUP_LAUNCH_COMMAND=.\setup.ps1" `
    -e "SETUP_RESUME_COMMAND=.\setup.ps1 --resume" `
    -e "SETUP_START_COMMAND=.\start.ps1" `
    -e "SETUP_STOP_COMMAND=.\stop.ps1" `
    api python -m scripts.bootstrap finalize-container-setup @finalizeArgs
exit $LASTEXITCODE
