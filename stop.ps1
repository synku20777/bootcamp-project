$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}
Set-Location $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "STOP COULD NOT CONTINUE: Docker is not installed or is not on PATH."
    exit 1
}

docker compose stop
if ($LASTEXITCODE -ne 0) {
    Write-Host "STOP COULD NOT CONTINUE: Docker could not stop the Compose services."
    Write-Host "Start Docker Desktop, inspect 'docker compose ps', and retry."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "SERVICES STOPPED SAFELY"
Write-Host "MongoDB and Redis volumes were preserved; Snowflake objects were unchanged."
Write-Host "Start again with: .\start.ps1"
