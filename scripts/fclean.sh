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

# 1. Run the standard clean script
if [ -f "$SCRIPT_DIR/clean.sh" ]; then
    bash "$SCRIPT_DIR/clean.sh"
fi

# 2. Clean cache folders
echo "[+] Removing local cache directories (.builthash, .output-nochanges)..."
rm -rf "$WORKSPACE_DIR"/.builthash
rm -rf "$WORKSPACE_DIR"/.output-nochanges

# 3. Clean root-level container caches if writable
if [ -d "/.builthash" ]; then
    echo "[+] Removing root-level container builthash cache..."
    rm -rf "/.builthash"
fi
if [ -d "/.output-nochanges" ]; then
    echo "[+] Removing root-level container output cache..."
    rm -rf "/.output-nochanges"
fi

# 4. Clean persistent cache directories
for opt_path in "/opt/pronze" "/opt/pronzeos"; do
    if [ -d "$opt_path" ]; then
        echo "[+] Cleaning persistent cache directory $opt_path..."
        rm -rf "$opt_path"/*
    fi
done

echo "=========================================================="
echo "          FCLEAN Completed Successfully!                  "
echo "=========================================================="
