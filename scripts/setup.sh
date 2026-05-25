#!/usr/bin/env bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/utils/log_lib.sh
source "$SCRIPT_DIR/utils/log_lib.sh"

# Target directory layout
OPT_DIR="${MEMFAULT_DIR:-/opt/PronKern}"
PROFILES_DIR="$OPT_DIR/profiles"
SDK_DIR="$OPT_DIR/sdk"
RUNTIME_DIR="$OPT_DIR/runtime"
SNAPSHOTS_DIR="$OPT_DIR/snapshots"

log_step "PronKern Setup: Initializing development environment"

# Install development packages if run as root on Debian/Ubuntu
if [ -f /etc/debian_version ] && [ "$(id -u)" -eq 0 ]; then
    log_step "Updating apt-get and installing build dependencies"
    apt-get update && apt-get install -y \
        build-essential \
        clang \
        llvm \
        lld \
        bison \
        flex \
        libssl-dev \
        bc \
        qemu-system \
        qemu-utils \
        btrfs-progs \
        libelf-dev \
        libncurses-dev \
        rsync \
        curl \
        wget \
        git
fi

# Create target directories
log_step "Creating system directories in $OPT_DIR"
mkdir -p "$OPT_DIR" "$PROFILES_DIR" "$SDK_DIR" "$RUNTIME_DIR" "$SNAPSHOTS_DIR" 2>/dev/null || {
    log_info "/opt permission denied. Attempting with sudo..."
    sudo mkdir -p "$OPT_DIR" "$PROFILES_DIR" "$SDK_DIR" "$RUNTIME_DIR" "$SNAPSHOTS_DIR"
    sudo chown -R "$(id -u):$(id -g)" "$OPT_DIR"
}

# Clone Linux Kernel
log_step "Checking for upstream Linux kernel repository"
cd "$OPT_DIR"
if [ ! -d linux ]; then
    log_step "Cloning Linux kernel (depth 1)"
    git clone --depth 1 https://github.com/torvalds/linux.git linux
else
    log_info "Linux kernel directory already exists. Skipping clone."
fi

# Clone BusyBox
log_step "Checking for upstream BusyBox repository"
if [ ! -d busybox ]; then
    log_step "Cloning BusyBox"
    git clone --depth 1 https://git.busybox.net/busybox busybox
else
    log_info "BusyBox directory already exists. Skipping clone."
fi

# Configure and compile BusyBox
log_step "Configuring and building BusyBox"
cd busybox
make defconfig
# Disable tc traffic shaping as requested
sed -i 's/CONFIG_TC=y/CONFIG_TC=n/' .config || true
make -j"$(nproc)"
make install

# Copy default profile
log_step "Populating default profiles"
mkdir -p "$PROFILES_DIR"
cat <<EOF > "$PROFILES_DIR/default.mfs"
{
  "allocation_failure_rate": 2,
  "fragmentation": true,
  "latency_ms": 5,
  "guard_pages": true,
  "corruption_rate": 0.01
}
EOF

log_success "Setup complete! Target files compiled in $OPT_DIR/busybox/_install"
