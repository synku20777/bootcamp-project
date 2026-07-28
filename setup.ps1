$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "SETUP COULD NOT CONTINUE"
    Write-Host "What happened: uv is not installed or is not on PATH."
    Write-Host "Likely cause: uv has not been installed, or this terminal was opened before installation completed."
    Write-Host "How to fix: Install uv from https://docs.astral.sh/uv/getting-started/installation/, reopen PowerShell, and verify with 'uv --version'."
    Write-Host "Then run: .\setup.ps1"
    Write-Host "Technical reference: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
}

uv lock --check
if ($LASTEXITCODE -ne 0) {
    Write-Host "SETUP COULD NOT CONTINUE"
    Write-Host "What happened: uv.lock does not match pyproject.toml."
    Write-Host "Likely cause: dependency declarations changed without committing a matching lockfile."
    Write-Host "How to fix: Restore the committed lockfile, or run 'uv lock' only if you intentionally changed dependencies."
    Write-Host "Then run: .\setup.ps1"
    Write-Host "Technical reference: pyproject.toml and uv.lock"
    exit $LASTEXITCODE
}
uv sync --locked
if ($LASTEXITCODE -ne 0) {
    Write-Host "SETUP COULD NOT CONTINUE"
    Write-Host "What happened: the locked Python environment could not be installed."
    Write-Host "Likely cause: package network access, disk space, or the uv cache is unavailable."
    Write-Host "How to fix: Check network/package access and free disk space, then retry."
    Write-Host "Then run: .\setup.ps1"
    Write-Host "Technical reference: the uv error immediately above"
    exit $LASTEXITCODE
}
uv run --locked python -m scripts.bootstrap setup @args
exit $LASTEXITCODE
