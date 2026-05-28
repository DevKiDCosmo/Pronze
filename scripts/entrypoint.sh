#!/usr/bin/env bash

# Dynamic entrypoint for PronzeOS Docker container.
# Configures persistent caching folders and routes to the requested target.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/utils/hash_helper.sh
source "$SCRIPT_DIR/utils/hash_helper.sh"

# Setup the persistent caching directory linking /.output-nochanges
setup_cache_dirs "/workspace"

# If first arg is empty, default to distro
if [ $# -eq 0 ]; then
    exec python3 /workspace/build-pipeline/pipeline.py --target distro
fi

TARGET="$1"
if [ "$TARGET" = "distro" ] || [ "$TARGET" = "module" ]; then
    shift
    exec python3 -u /workspace/build-pipeline/pipeline.py --target "$TARGET" "$@"
elif [[ "$TARGET" == -* ]]; then
    # First arg is an option, so default target to distro and pass all options
    exec python3 -u /workspace/build-pipeline/pipeline.py --target distro "$@"
else
    # Fallback to custom command (e.g. bash, fclean.sh)
    exec "$@"
fi

