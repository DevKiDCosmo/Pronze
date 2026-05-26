#!/usr/bin/env bash

# Pron OS Developer Workspace Cleanup Script
# Deletes all compiled binaries, intermediate objects, logs, and build artifacts.

set -e

# Resolve script directory and workspace root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================================="
echo "          Pron OS Developer Workspace Cleanup            "
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
rm -f "$WORKSPACE_DIR"/test/pronmf.zig
rm -f "$WORKSPACE_DIR"/test_zig
rm -f "$WORKSPACE_DIR"/test_zig.o

# 5. Clean kernel module build artifacts
echo "[+] Cleaning kernel module build artifacts..."
if [ -d "$WORKSPACE_DIR/kernel" ]; then
    rm -f "$WORKSPACE_DIR"/kernel/*.o
    rm -f "$WORKSPACE_DIR"/kernel/*.ko
    rm -f "$WORKSPACE_DIR"/kernel/*.mod
    rm -f "$WORKSPACE_DIR"/kernel/*.mod.c
    rm -f "$WORKSPACE_DIR"/kernel/*.cmd
    rm -f "$WORKSPACE_DIR"/kernel/modules.order
    rm -f "$WORKSPACE_DIR"/kernel/Module.symvers
    rm -rf "$WORKSPACE_DIR"/kernel/.tmp_versions
    rm -f "$WORKSPACE_DIR"/kernel/.*.cmd
fi

# 6. Clean host system logs or other temporary artifacts in workspace
echo "[+] Removing build logs..."
rm -f "$WORKSPACE_DIR"/out.log

# 7. Clean temporary build directory if it exists on host (if running in builder)
if [ -d "/tmp/memfault_build" ]; then
    echo "[+] Cleaning temporary build directory /tmp/memfault_build..."
    rm -rf /tmp/memfault_build
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
