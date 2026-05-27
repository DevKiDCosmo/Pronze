#!/usr/bin/env bash

# PronzeOS USB Flashing Assistant Script
# Safe wrapper around dd for macOS and Linux.

set -e

IMAGE_PATH="/workspace/output/pronzeos.img"
if [ ! -f "$IMAGE_PATH" ]; then
    IMAGE_PATH="./output/pronzeos.img"
fi

echo "=========================================================="
echo "          PronzeOS USB Flashing Assistant                  "
echo "=========================================================="

if [ ! -f "$IMAGE_PATH" ]; then
    echo "[-] Error: Bootable image not found at $IMAGE_PATH."
    echo "    Please run the build pipeline first."
    exit 1
fi

echo "[+] Found bootable image at: $IMAGE_PATH"
echo "[+] Size: $(du -sh "$IMAGE_PATH" | cut -f1)"

# Detect OS
OS_TYPE="$(uname -s)"
echo "[+] Operating System: $OS_TYPE"

# 1. Scan and List Disks
echo -e "\n--- Scanning for available disks ---"
if [ "$OS_TYPE" = "Darwin" ]; then
    diskutil list
else
    if command -v lsblk >/dev/null 2>&1; then
        lsblk -d -o NAME,SIZE,MODEL,TRAN | grep -v "loop" || true
    else
        sudo fdisk -l | grep "Disk /dev/" || true
    fi
fi
echo "------------------------------------"

# 2. Prompt for Target Disk
echo ""
read -p "Enter the target disk identifier (e.g. disk3 or sdb): " DISK_ID

if [ -z "$DISK_ID" ]; then
    echo "[-] Error: No disk identifier entered."
    exit 1
fi

# Clean up input path if user entered /dev/...
DISK_ID="${DISK_ID#/dev/}"
DISK_ID="${DISK_ID#r}" # remove r for raw disk on macOS if entered

TARGET_DEV="/dev/$DISK_ID"
RAW_DEV=""

# Validate disk existence
if [ "$OS_TYPE" = "Darwin" ]; then
    if ! diskutil info "$TARGET_DEV" >/dev/null 2>&1; then
        echo "[-] Error: Device $TARGET_DEV does not exist or is invalid."
        exit 1
    fi
    RAW_DEV="/dev/r$DISK_ID"
else
    if [ ! -b "$TARGET_DEV" ]; then
        echo "[-] Error: Device $TARGET_DEV does not exist or is not a block device."
        exit 1
    fi
    RAW_DEV="$TARGET_DEV"
fi

# Get Disk details for warning
echo -e "\n[!] WARNING: YOU HAVE SELECTED:"
if [ "$OS_TYPE" = "Darwin" ]; then
    diskutil info "$TARGET_DEV" | grep -E "Device / Media Name|Total Size" || true
else
    if command -v lsblk >/dev/null 2>&1; then
        lsblk -d -o NAME,SIZE,MODEL "$TARGET_DEV" || true
    else
        sudo fdisk -l "$TARGET_DEV" | head -n 1 || true
    fi
fi

echo -e "\n=========================================================="
echo "  DANGER: THIS WILL ERASE ALL DATA ON $TARGET_DEV!"
echo "  Make sure you have selected the correct drive."
echo "=========================================================="
read -p "Type 'CONFIRM-WRITE' to proceed: " CONFIRM

if [ "$CONFIRM" != "CONFIRM-WRITE" ]; then
    echo "[-] Flashing cancelled. No changes were made."
    exit 1
fi

# 3. Unmount Disk
echo -e "\n[+] Unmounting target disk partitions..."
if [ "$OS_TYPE" = "Darwin" ]; then
    diskutil unmountDisk "$TARGET_DEV"
else
    # Try unmounting all partitions on Linux
    for part in "$TARGET_DEV"*; do
        if [ -b "$part" ]; then
            sudo umount "$part" 2>/dev/null || true
        fi
    done
fi

# 4. Flash Image using dd
echo "[+] Starting write operation (this may take a few minutes)..."
if [ "$OS_TYPE" = "Darwin" ]; then
    echo "[+] Executing: sudo dd if=$IMAGE_PATH of=$RAW_DEV bs=1m"
    sudo dd if="$IMAGE_PATH" of="$RAW_DEV" bs=1m
else
    echo "[+] Executing: sudo dd if=$IMAGE_PATH of=$RAW_DEV bs=4M status=progress conv=fsync"
    sudo dd if="$IMAGE_PATH" of="$RAW_DEV" bs=4M status=progress conv=fsync
fi

# 5. Eject / Sync
echo "[+] Syncing and finalizing..."
if [ "$OS_TYPE" = "Darwin" ]; then
    diskutil eject "$TARGET_DEV"
    echo "[✔] Success! You can now unplug the USB drive and boot from it."
else
    sync
    echo "[✔] Success! You can now unplug the USB drive and boot from it."
fi
