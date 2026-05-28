#!/usr/bin/env bash

# Thin wrapper routing to the Python DAG pipeline distro target
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

exec python3 "$BASE_DIR/build-pipeline/pipeline.py" --target distro "$@"
