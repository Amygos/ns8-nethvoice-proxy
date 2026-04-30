#!/usr/bin/env bash
# Manual one-shot wrapper around the pytest-driven e2e suite.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v podman-compose >/dev/null; then
    echo "podman-compose is required" >&2; exit 1
fi
if ! command -v pytest >/dev/null; then
    echo "pytest is required (pip install pytest)" >&2; exit 1
fi

exec pytest -q "$@"
