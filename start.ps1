$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not installed. Install uv, reopen PowerShell, and run this file again."
    exit 1
}

uv run --locked python -m scripts.bootstrap start @args
exit $LASTEXITCODE
