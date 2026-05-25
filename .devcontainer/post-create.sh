#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../scripts/utils/log_lib.sh
source "$SCRIPT_DIR/../scripts/utils/log_lib.sh"

log_section "PronKern Dev Container Ready" 58

mkdir -p build logs output

KDIR="/lib/modules/$(uname -r)/build"
if [ -d "$KDIR" ]; then
    log_success "Kernel build tree found: $KDIR"
    log_info "Build the module with: make -C \"$KDIR\" M=\"$PWD/kernel\" modules"
else
    log_warn "Kernel build tree not found at: $KDIR"
    log_warn "On Linux hosts, bind-mount matching headers or set KDIR manually before building."
fi

log_info "Suggested in-container command: make -C /lib/modules/\$(uname -r)/build M=\"$PWD/kernel\" modules"
