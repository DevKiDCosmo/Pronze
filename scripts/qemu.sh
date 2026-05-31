#!/usr/bin/env bash

set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_PATH="$BASE_DIR/output/pronzeos.img"

if [ ! -f "$IMAGE_PATH" ]; then
    echo "[-] Error: Disk image not found at $IMAGE_PATH"
    echo "    Please run the Docker build container first to generate the image."
    exit 1
fi

# Locate UEFI/OVMF firmware dynamically
echo "[+] Locating UEFI / OVMF firmware..."
OVMF_PATH=""
for path in \
    "/opt/homebrew/share/qemu/edk2-x86_64-code.fd" \
    "/opt/homebrew/share/qemu/OVMF.fd" \
    "/usr/share/OVMF/OVMF_CODE.fd" \
    "/usr/share/ovmf/OVMF.fd" \
    "/usr/share/qemu/OVMF.fd" \
    "/opt/local/share/qemu/edk2-x86_64-code.fd" \
    "/usr/share/ovmf/x64/OVMF_CODE.fd"
do
    if [ -f "$path" ]; then
        OVMF_PATH="$path"
        break
    fi
done

if [ -z "$OVMF_PATH" ]; then
    # Dynamic search fallback
    OVMF_PATH=$(find /opt/homebrew/share/qemu /usr/share/OVMF /usr/share/ovmf /usr/share/qemu -name "edk2-x86_64-code.fd" -o -name "OVMF.fd" -o -name "OVMF_CODE.fd" 2>/dev/null | head -n 1 || true)
fi

if [ -z "$OVMF_PATH" ]; then
    echo "[-] Warning: UEFI / OVMF firmware file not found."
    echo "    systemd-boot requires a UEFI environment. QEMU may fail to boot the partition table."
    echo "    To resolve this:"
    echo "      - macOS: Run 'brew install qemu' (contains edk2 firmware files)"
    echo "      - Ubuntu/Debian: Run 'sudo apt install ovmf'"
    echo "----------------------------------------------------------"
else
    echo "[✔] Found UEFI Firmware: $OVMF_PATH"
fi

# Detect Host Platform and select proper hypervisor acceleration
ACCEL=""
OS_TYPE=$(uname -s)

if [ "$OS_TYPE" = "Darwin" ]; then
    echo "[+] Detected macOS host. Using Hypervisor.framework (HVF) acceleration."
    # UEFI systemd-boot boots x86_64 images, which runs under TCG emulation on Apple Silicon.
    # On Intel Mac, HVF can accelerate it.
    ARCH=$(uname -m)
    if [ "$ARCH" = "x86_64" ]; then
        ACCEL="-accel hvf -cpu Penryn"
    else
        echo "[i] Running x86_64 guest on Apple Silicon (ARM64) host. TCG emulation will be used."
        ACCEL="-cpu max"
    fi
elif [ "$OS_TYPE" = "Linux" ]; then
    if [ -r /dev/kvm ]; then
        echo "[+] Detected Linux host with KVM access. Using KVM acceleration."
        ACCEL="-accel kvm -cpu host"
    else
        echo "[i] Detected Linux host but /dev/kvm is not readable. Running without KVM."
        ACCEL=""
    fi
fi

# Parse GUI option
GUI_MODE="nographic"
for arg in "$@"; do
    if [ "$arg" = "--gui" ]; then
        GUI_MODE="gui"
    fi
done

echo "=========================================================="
echo "          Launching Pronze inside QEMU (UEFI)               "
echo "=========================================================="
echo "[+] Image:       $IMAGE_PATH"
echo "[+] Memory:      1024 MB"
echo "[+] Console:     $GUI_MODE"

BIOS_ARG=""
if [ -n "$OVMF_PATH" ]; then
    BIOS_ARG="-drive if=pflash,format=raw,unit=0,file=$OVMF_PATH,readonly=on"
fi

if [ "$GUI_MODE" = "gui" ]; then
    # Graphical GUI mode (opens a window, choose "VGA Graphics" boot menu option)
    echo "[i] Graphical window booting. In systemd-boot menu select 'VGA Graphics'"
    qemu-system-x86_64 \
        $ACCEL \
        $BIOS_ARG \
        -m 1G \
        -hda "$IMAGE_PATH"
else
    # Terminal redirect console mode (runs directly in your shell, choose "Serial Console" boot menu option)
    echo "[i] Headless booting. In systemd-boot menu select 'Serial Console' (Default)"
    echo "[i] Press Ctrl-A then X to terminate QEMU"
    sleep 1
    qemu-system-x86_64 \
        $ACCEL \
        $BIOS_ARG \
        -m 1G \
        -hda "$IMAGE_PATH" \
        -nographic \
        -serial mon:stdio
fi
