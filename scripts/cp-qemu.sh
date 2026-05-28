#!/usr/bin/env bash

# PronzeOS QEMU Disk File Extractor (cp-qemu)
# Copies a file or directory from the virtual machine's Btrfs filesystem to the host.

set -e

# Resolve script directory and workspace root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_PATH="$WORKSPACE_DIR/output/pronzeos.img"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <path_inside_qemu_rootfs> [destination_path_on_host]"
    echo "Example: $0 /etc/s6-services/pronze/run ./extracted_run"
    exit 1
fi

SRC_PATH="$1"
# Strip leading slash from source path inside QEMU if present
SRC_PATH="${SRC_PATH#/}"

# Default destination is the current working directory on host
DEST_PATH="${2:-.}"

if [ ! -f "$IMAGE_PATH" ]; then
    echo "[-] Error: Disk image not found at $IMAGE_PATH"
    echo "    Please run the build pipeline first to generate the disk image."
    exit 1
fi

echo "[+] Target Image:      output/pronzeos.img"
echo "[+] Copying path:      /$SRC_PATH"
echo "[+] Destination:       $DEST_PATH"
echo "[+] Starting copy using temporary docker container mount..."

# Run temporary container with --privileged to mount the btrfs partition
docker run --rm --privileged \
    -v "$WORKSPACE_DIR:/workspace" \
    ubuntu:24.04 /bin/bash -c "
        # Silence package installs
        apt-get update -y &>/dev/null && apt-get install -y btrfs-progs &>/dev/null
        
        # Btrfs partition starts at sector 133120
        # Sector size = 512 bytes => offset = 68157440 bytes
        mkdir -p /tmp/mount_qemu
        if mount -o loop,offset=68157440 /workspace/output/pronzeos.img /tmp/mount_qemu 2>/dev/null; then
            if [ -e \"/tmp/mount_qemu/$SRC_PATH\" ]; then
                # Copy from mount to workspace mapping
                cp -rp \"/tmp/mount_qemu/$SRC_PATH\" \"/workspace/$DEST_PATH\"
                echo '[✔] File/directory copied successfully!'
                umount /tmp/mount_qemu
                exit 0
            else
                echo \"[-] Error: Path '/$SRC_PATH' not found inside QEMU Btrfs rootfs.\"
                umount /tmp/mount_qemu
                exit 1
            fi
        else
            echo '[-] Error: Failed to mount Btrfs partition from output/pronzeos.img'
            exit 1
        fi
    "
