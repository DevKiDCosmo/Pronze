#!/usr/bin/env bash

# PronzeOS Full Workspace Cleanup Script (fclean)
# Deletes all compiled binaries, intermediate objects, logs, build hashes, and persistent caches.

set -e

# Resolve script directory and workspace root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================================="
echo "          PronzeOS Full Workspace Cleanup (FCLEAN)        "
echo "=========================================================="

# 1. Parse arguments
REMOVE_TARS=false
for arg in "$@"; do
    if [ "$arg" = "-k" ] || [ "$arg" = "--kill-tars" ]; then
        REMOVE_TARS=true
    fi
done

# 2. Run the standard clean script
if [ -f "$SCRIPT_DIR/clean.sh" ]; then
    bash "$SCRIPT_DIR/clean.sh"
fi

# 3. Clean cache folders
echo "[+] Removing local cache directory (.output-nochanges)..."
rm -rf "$WORKSPACE_DIR"/.output-nochanges
rm -rf "$WORKSPACE_DIR"/.builthash 2>/dev/null || true

# 4. Clean root-level container caches if writable
if [ -d "/.output-nochanges" ]; then
    echo "[+] Removing root-level container output cache..."
    rm -rf "/.output-nochanges"
fi
if [ -d "/.builthash" ]; then
    rm -rf "/.builthash" 2>/dev/null || true
fi

# 5. Clean persistent cache directories
for opt_path in "/opt/pronze" "/opt/pronzeos" "/opt/pronkern"; do
    if [ -d "$opt_path" ]; then
        if [ "$REMOVE_TARS" = "true" ]; then
            echo "[+] Cleaning ALL files and tarballs in persistent cache directory $opt_path..."
            find "$opt_path" -mindepth 1 -delete 2>/dev/null || rm -rf "$opt_path"/*
        else
            echo "[+] Cleaning extracted sources in persistent cache directory $opt_path (preserving downloads & cached tarballs)..."
            rm -rf "$opt_path"/src
        fi
    fi
done

# 6. If running on host (not in Docker) and docker command exists, clean Docker persistent cache volumes
if [ ! -f /.dockerenv ] && command -v docker &>/dev/null; then
    WS_BASE="$(basename "$WORKSPACE_DIR")"
    if [ "$REMOVE_TARS" = "true" ]; then
        echo "[+] Detected host environment. Purging Docker cache volumes..."
        
        # Purge standard volumes
        docker run --rm -v pronkern-cache:/opt/pronkern ubuntu:24.04 find /opt/pronkern -mindepth 1 -delete 2>/dev/null || true
        docker run --rm -v pronze-cache:/opt/pronze ubuntu:24.04 find /opt/pronze -mindepth 1 -delete 2>/dev/null || true
        docker volume rm pronkern-cache 2>/dev/null || true
        docker volume rm pronze-cache 2>/dev/null || true
        
        # Purge worktree-specific volumes
        if [ "$WS_BASE" != "PronKern" ] && [ -n "$WS_BASE" ]; then
            echo "[+] Purging worktree-specific Docker cache volumes for $WS_BASE..."
            docker run --rm -v "pronkern-cache-${WS_BASE}:/opt/pronkern" ubuntu:24.04 find /opt/pronkern -mindepth 1 -delete 2>/dev/null || true
            docker run --rm -v "pronze-cache-${WS_BASE}:/opt/pronze" ubuntu:24.04 find /opt/pronze -mindepth 1 -delete 2>/dev/null || true
            docker volume rm "pronkern-cache-${WS_BASE}" 2>/dev/null || true
            docker volume rm "pronze-cache-${WS_BASE}" 2>/dev/null || true
        fi
    else
        echo "[+] Detected host environment. Cleaning extracted sources inside Docker cache volumes..."
        docker run --rm -v pronkern-cache:/opt/pronkern ubuntu:24.04 rm -rf /opt/pronkern/src 2>/dev/null || true
        docker run --rm -v pronze-cache:/opt/pronze ubuntu:24.04 rm -rf /opt/pronze/src 2>/dev/null || true
        
        if [ "$WS_BASE" != "PronKern" ] && [ -n "$WS_BASE" ]; then
            echo "[+] Cleaning worktree-specific Docker cache volumes for $WS_BASE..."
            docker run --rm -v "pronkern-cache-${WS_BASE}:/opt/pronkern" ubuntu:24.04 rm -rf /opt/pronkern/src 2>/dev/null || true
            docker run --rm -v "pronze-cache-${WS_BASE}:/opt/pronze" ubuntu:24.04 rm -rf /opt/pronze/src 2>/dev/null || true
        fi
    fi
fi

echo "=========================================================="
echo "          FCLEAN Completed Successfully!                  "
echo "=========================================================="
