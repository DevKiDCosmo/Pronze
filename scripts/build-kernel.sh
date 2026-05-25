#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/utils/log_lib.sh
source "$SCRIPT_DIR/utils/log_lib.sh"

KERNEL_DIR="${KERNEL_DIR:-/opt/PronKern/linux}"

if [ ! -d "$KERNEL_DIR" ]; then
    log_error "Kernel directory $KERNEL_DIR not found. Run scripts/setup.sh first."
    exit 1
fi

cd "$KERNEL_DIR"

log_step "Generiere Standardkonfiguration"
make defconfig

log_step "Aktiviere PronKern Instrumentation-Funktionen"
scripts/config --enable CONFIG_BPF
scripts/config --enable CONFIG_KPROBES
scripts/config --enable CONFIG_FTRACE
scripts/config --enable CONFIG_FAULT_INJECTION
scripts/config --enable CONFIG_PERF_EVENTS
scripts/config --enable CONFIG_KALLSYMS
scripts/config --enable CONFIG_KASAN
scripts/config --enable CONFIG_DEBUG_FS

log_step "Aktiviere Kernel Safety & Prevention-Konfigurationen"
scripts/config --disable CONFIG_PANIC_ON_OOPS
scripts/config --disable CONFIG_BUG
scripts/config --enable CONFIG_DEBUG_KERNEL

log_step "Kompiliere den angepassten Kernel"
make -j"$(nproc)"

log_step "Installiere Kernel-Module und Artefakte"
# Run modules_install if permissions allow
if [ "$(id -u)" -eq 0 ]; then
    make modules_install
    make install
else
    log_info "Non-root user: modules_install/install wird übersprungen. Binärdatei liegt in arch/x86/boot/bzImage"
fi

log_success "PronKern Kernel Build Complete!"
