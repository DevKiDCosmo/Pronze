#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/utils/log_lib.sh
source "$SCRIPT_DIR/utils/log_lib.sh"

REPO_DIR="${REPO_DIR:-/workspace/linux}"
KERNEL_REPO="${KERNEL_REPO:-https://github.com/torvalds/linux.git}"

log_step "Kernel-Repository klonen"
rm -rf "$REPO_DIR"
git clone --depth 1 "$KERNEL_REPO" "$REPO_DIR"

cd "$REPO_DIR"
log_step "Kernel-Standardkonfiguration erzeugen"
make defconfig
log_step "Kernel bauen"
make -j"$(nproc)"

log_success "Kernel build complete"