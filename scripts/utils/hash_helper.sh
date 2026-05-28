#!/usr/bin/env bash

# Shared hashing and caching utility functions for PronzeOS

# Find all files in the directory (excluding build directories/objects) and calculate a composite hash.
get_dir_hash() {
    local dir=$1
    if [ ! -d "$dir" ]; then
        echo ""
        return
    fi
    
    local hash_cmd="sha256sum"
    if ! command -v sha256sum &>/dev/null; then
        if command -v shasum &>/dev/null; then
            hash_cmd="shasum -a 256"
        else
            hash_cmd="md5"
        fi
    fi

    # Find all files, sort paths to guarantee stability, and compute the hash
    local file_list
    file_list=$(find "$dir" -type f \
        -not -path '*/.*' \
        -not -path '*/target/*' \
        -not -name '*.o' \
        -not -name '*.ko' \
        -not -name '*.mod*' \
        -not -name '*.o.d' \
        -not -name '*.so' \
        -not -name 'test_alloc' \
        -not -name 'test_bounds' \
        -not -name 'test_zig' \
        -not -name 'test_rust' \
        -not -name '*.img' \
        -not -name 'modules.order' \
        -not -name 'Module.symvers' \
        | sort)

    if [ -z "$file_list" ]; then
        echo "empty"
        return
    fi

    # Pass the list of files to get their hashes, then hash the hashes for a final composite hash.
    echo "$file_list" | xargs $hash_cmd 2>/dev/null | $hash_cmd | cut -d' ' -f1
}

# Computes a hash for a single file.
get_file_hash() {
    local file=$1
    if [ ! -f "$file" ]; then
        echo ""
        return
    fi

    local hash_cmd="sha256sum"
    if ! command -v sha256sum &>/dev/null; then
        if command -v shasum &>/dev/null; then
            hash_cmd="shasum -a 256"
        else
            hash_cmd="md5"
        fi
    fi

    $hash_cmd "$file" | cut -d' ' -f1
}

# Resolves directory configuration for caching
setup_cache_dirs() {
    local base_dir=$1
    NOCHANGES_DIR="$base_dir/.output-nochanges"
    mkdir -p "$NOCHANGES_DIR"

    # Create root-level symlinks inside the container for compatibility if root is writable
    if mkdir -p "/.output-nochanges" 2>/dev/null; then
        rm -rf "/.output-nochanges"
        ln -sf "$NOCHANGES_DIR" "/.output-nochanges"
    fi

    # Migrate any legacy files from .builthash to .output-nochanges
    if [ -d "$base_dir/.builthash" ]; then
        cp -af "$base_dir/.builthash"/* "$NOCHANGES_DIR/" 2>/dev/null || true
        rm -rf "$base_dir/.builthash"
    fi
    if [ -L "/.builthash" ] || [ -d "/.builthash" ]; then
        rm -rf "/.builthash"
    fi
}
