#!/usr/bin/env bash

# PronzeOS Developer Workspace Cleanup Script
# Deletes all compiled binaries, intermediate objects, logs, and build artifacts.

set -e

# Resolve script directory and workspace root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================================="
echo "          PronzeOS Developer Workspace Cleanup            "
echo "=========================================================="

# 1. Clean output directory
if [ -d "$WORKSPACE_DIR/output" ]; then
    echo "[+] Cleaning $WORKSPACE_DIR/output/..."
    rm -rf "$WORKSPACE_DIR"/output/*
else
    echo "[+] Creating $WORKSPACE_DIR/output/..."
    mkdir -p "$WORKSPACE_DIR/output"
fi

# 2. Clean cargo target directories
echo "[+] Cleaning Rust daemon build target..."
if [ -d "$WORKSPACE_DIR/daemon" ]; then
    (cd "$WORKSPACE_DIR/daemon" && cargo clean 2>/dev/null || rm -rf target)
fi

echo "[+] Cleaning Rust test build target..."
if [ -d "$WORKSPACE_DIR/test/test_rust" ]; then
    (cd "$WORKSPACE_DIR/test/test_rust" && cargo clean 2>/dev/null || rm -rf target)
fi

# 3. Clean compiled SDK shared libraries
echo "[+] Removing compiled C SDK libraries..."
rm -f "$WORKSPACE_DIR"/sdk/c/src/*.so

# 4. Clean compiled test binaries and intermediate files
echo "[+] Removing compiled test binaries and intermediate files..."
rm -f "$WORKSPACE_DIR"/test/test_alloc
rm -f "$WORKSPACE_DIR"/test/test_bounds
rm -f "$WORKSPACE_DIR"/test/test_zig
rm -f "$WORKSPACE_DIR"/test/test_zig.o
rm -f "$WORKSPACE_DIR"/test/pronze.zig
rm -f "$WORKSPACE_DIR"/test_zig
rm -f "$WORKSPACE_DIR"/test_zig.o

# 5. Clean kernel module and compilation artifacts recursively
echo "[+] Cleaning compilation and kernel module build artifacts recursively..."
find "$WORKSPACE_DIR" -path "*/.git" -prune -o -type f \( \
    -name "*.o" -o \
    -name "*.ko" -o \
    -name "*.mod" -o \
    -name "*.mod.c" -o \
    -name "*.cmd" -o \
    -name ".*.cmd" -o \
    -name "modules.order" -o \
    -name "Module.symvers" \
\) -exec rm -f {} +

find "$WORKSPACE_DIR" -path "*/.git" -prune -o -type d -name ".tmp_versions" -exec rm -rf {} +


# 6. Clean host system logs or other temporary artifacts in workspace
echo "[+] Removing build logs..."
rm -f "$WORKSPACE_DIR"/out.log

# 7. Clean temporary build directory if it exists on host (if running in builder)
if [ -d "/tmp/pronze_build" ]; then
    echo "[+] Cleaning temporary build directory /tmp/pronze_build..."
    rm -rf /tmp/pronze_build
fi
if [ -d "/tmp/s6_install" ]; then
    echo "[+] Cleaning temporary s6 install directory /tmp/s6_install..."
    rm -rf /tmp/s6_install
fi
if [ -d "/tmp/musl_libs" ]; then
    echo "[+] Cleaning temporary musl libs /tmp/musl_libs..."
    rm -rf /tmp/musl_libs
fi

echo "=========================================================="
echo "          Cleanup Completed Successfully!                 "
echo "=========================================================="
