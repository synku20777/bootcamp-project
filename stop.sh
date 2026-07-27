#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. Install uv, reopen the terminal, and run this file again."
    exit 1
fi

uv run --locked python -m scripts.bootstrap stop "$@"
