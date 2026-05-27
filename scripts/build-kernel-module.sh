#!/usr/bin/env bash

# PronzeOS: Fast compilation script for the kernel module only.
# Skips full distro compilation. Integrates hash checking and caching.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/utils/log_lib.sh
source "$SCRIPT_DIR/utils/log_lib.sh"
# shellcheck source=scripts/utils/hash_helper.sh
source "$SCRIPT_DIR/utils/hash_helper.sh"

BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

log_section "       PronzeOS Fast Kernel Module Build       " 58

# Source Version Configuration
if [ -f "$BASE_DIR/pipeline.conf" ]; then
    source "$BASE_DIR/pipeline.conf"
else
    log_error "pipeline.conf not found."
    exit 1
fi

# Resolve header version to download and compile against
HEADERS_VERSION="${LINUX_HEADERS_VERSION:-$LINUX_VERSION}"
log_info "Target Kernel Headers Version: $HEADERS_VERSION"

# Configure directories
OPT_DIR="${PRONZE_DIR:-/opt/pronze}"
DOWNLOAD_DIR="$OPT_DIR/downloads"
SRC_DIR="$OPT_DIR/src"
OUTPUT_DIR="$BASE_DIR/output"

setup_cache_dirs "$BASE_DIR"
mkdir -p "$OPT_DIR" "$DOWNLOAD_DIR" "$SRC_DIR" "$OUTPUT_DIR"

# Check hashes to see if source has changed
KERNEL_HASH=$(get_dir_hash "$BASE_DIR/kernel")
SAVED_HASH_FILE="$BUILTHASH_DIR/kernel.hash"
CACHED_KO="$NOCHANGES_DIR/pronze.ko"

if [ -f "$SAVED_HASH_FILE" ] && [ -f "$CACHED_KO" ]; then
    SAVED_HASH=$(cat "$SAVED_HASH_FILE")
    if [ "$KERNEL_HASH" = "$SAVED_HASH" ]; then
        log_success "Kernel module source unchanged. Copying cached pronze.ko to output..."
        cp -av "$CACHED_KO" "$OUTPUT_DIR/"
        log_success "Done!"
        exit 0
    fi
fi

# Kernel source setup with Git download & fallback
KERNEL_SRC_DIR="$SRC_DIR/linux-$HEADERS_VERSION"
if [ ! -d "$KERNEL_SRC_DIR" ]; then
    log_step "Kernel headers source not found locally at $KERNEL_SRC_DIR."
    
    # Try Git clone
    CLONE_SUCCESS=false
    if command -v git &>/dev/null; then
        log_step "Attempting to clone Linux Kernel v$HEADERS_VERSION using Git..."
        if git clone --depth 1 --branch "v$HEADERS_VERSION" https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git "$KERNEL_SRC_DIR"; then
            log_success "Kernel cloned successfully using Git!"
            CLONE_SUCCESS=true
        else
            log_warn "Git clone failed. Falling back to downloading tarball..."
        fi
    else
        log_warn "Git not found. Falling back to downloading tarball..."
    fi

    # Fallback if Git clone didn't happen
    if [ "$CLONE_SUCCESS" = "false" ]; then
        TARBALL="$DOWNLOAD_DIR/linux-$HEADERS_VERSION.tar.xz"
        if [ ! -f "$TARBALL" ]; then
            log_step "Downloading kernel tarball from cdn.kernel.org..."
            wget -c "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-$HEADERS_VERSION.tar.xz" -O "$TARBALL"
        fi
        log_step "Extracting kernel source..."
        tar -xf "$TARBALL" -C "$SRC_DIR"
    fi
fi

cd "$KERNEL_SRC_DIR"

# Configure if not already configured
if [ ! -f .config ]; then
    log_step "Configuring kernel source (defconfig)..."
    make defconfig
fi

# Run modules_prepare if UTS Release not generated yet
if [ ! -f include/generated/utsrelease.h ]; then
    log_step "Preparing kernel headers/dependencies for module build..."
    make modules_prepare -j"$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)"
fi

# Compile the module
log_step "Compiling out-of-tree pronze module..."
make KBUILD_MODPOST_WARN=1 -C "$KERNEL_SRC_DIR" M="$BASE_DIR/kernel" modules

# Verify and save
KO_PATH="$BASE_DIR/kernel/pronze.ko"
if [ -f "$KO_PATH" ]; then
    log_success "Kernel module compiled successfully: $KO_PATH"
    cp -av "$KO_PATH" "$OUTPUT_DIR/"
    cp -av "$KO_PATH" "$CACHED_KO"
    echo "$KERNEL_HASH" > "$SAVED_HASH_FILE"
    log_success "Saved hash and cached pronze.ko"
else
    log_error "Could not find compiled kernel module at $KO_PATH"
    exit 1
fi
