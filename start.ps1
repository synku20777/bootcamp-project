$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}
Set-Location $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "START COULD NOT CONTINUE: Docker is not installed or is not on PATH."
    Write-Host "Install Docker Desktop, reopen PowerShell, and run .\start.ps1 again."
    exit 1
}
if (-not (Test-Path ".env")) {
    Write-Host "START COULD NOT CONTINUE: first-time setup has not created .env."
    Write-Host "Run .\setup.ps1 first."
    exit 1
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "START COULD NOT CONTINUE: Docker Desktop is not running."
    Write-Host "Start Docker Desktop, verify with 'docker info', and retry."
    exit 1
}

docker compose up -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) {
    Write-Host "START COULD NOT CONTINUE: one or more services did not become healthy."
    Write-Host "Run 'docker compose ps' and 'docker compose logs <service>'."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "SERVICES STARTED SUCCESSFULLY"
Write-Host "Dashboard: http://localhost:8050/overview"
Write-Host "API documentation: http://localhost:8000/docs"
Write-Host "Snowflake was not queried by this start command."
Write-Host "Stop safely with: .\stop.ps1"
