#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v uv >/dev/null 2>&1; then
    echo "SETUP COULD NOT CONTINUE"
    echo "What happened: uv is not installed or is not on PATH."
    echo "Likely cause: uv has not been installed, or this terminal was opened before installation completed."
    echo "How to fix: Install uv from https://docs.astral.sh/uv/getting-started/installation/, reopen the terminal, and verify with 'uv --version'."
    echo "Then run: ./setup.sh"
    echo "Technical reference: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

if ! uv lock --check; then
    echo "SETUP COULD NOT CONTINUE"
    echo "What happened: uv.lock does not match pyproject.toml."
    echo "Likely cause: dependency declarations changed without committing a matching lockfile."
    echo "How to fix: Restore the committed lockfile, or run 'uv lock' only if you intentionally changed dependencies."
    echo "Then run: ./setup.sh"
    echo "Technical reference: pyproject.toml and uv.lock"
    exit 1
fi
if ! uv sync --locked; then
    echo "SETUP COULD NOT CONTINUE"
    echo "What happened: the locked Python environment could not be installed."
    echo "Likely cause: package network access, disk space, or the uv cache is unavailable."
    echo "How to fix: Check network/package access and free disk space, then retry."
    echo "Then run: ./setup.sh"
    echo "Technical reference: the uv error immediately above"
    exit 1
fi
uv run --locked python -m scripts.bootstrap setup "$@"
