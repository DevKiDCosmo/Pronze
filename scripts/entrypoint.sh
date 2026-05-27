#!/usr/bin/env bash

# Dynamic entrypoint for PronzeOS Docker container.
# Configures persistent caching folders and routes to the requested target.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/utils/hash_helper.sh
source "$SCRIPT_DIR/utils/hash_helper.sh"

# Setup the persistent caching directories linking /.builthash and /.output-nochanges
setup_cache_dirs "/workspace"

TARGET=${1:-distro}

# Route the build target
if [ "$TARGET" = "module" ]; then
    exec /bin/bash /workspace/scripts/build-kernel-module.sh
elif [ "$TARGET" = "distro" ]; then
    exec /bin/bash /workspace/scripts/build-distro.sh
else
    # Fallback to custom command if any other args were passed
    exec "$@"
fi
