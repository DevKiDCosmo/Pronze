#!/usr/bin/env bash

set -e

echo "=========================================================="
echo "          MemFaultOS Bootable Distro Build Pipeline       "
echo "=========================================================="

# Source Version Configuration
if [ -f "/workspace/pipeline.conf" ]; then
    echo "[+] Loading version config from pipeline.conf..."
    source /workspace/pipeline.conf
else
    echo "[-] Error: pipeline.conf not found."
    exit 1
fi

echo "  - Linux Kernel:  $LINUX_VERSION"
echo "  - BusyBox:       $BUSYBOX_VERSION"
echo "  - s6 Init:       $S6_VERSION"

# Configure directories
OPT_DIR="${MEMFAULT_DIR:-/opt/memfaultos}"
DOWNLOAD_DIR="$OPT_DIR/downloads"
SRC_DIR="$OPT_DIR/src"
OUTPUT_DIR="/workspace/output"
WORK_DIR="/tmp/memfault_build"
ROOTFS_DIR="$WORK_DIR/rootfs"
MUSL_LIBS_DIR="/tmp/musl_libs"

echo "[+] Creating build directories..."
mkdir -p "$OPT_DIR" "$DOWNLOAD_DIR" "$SRC_DIR" "$OUTPUT_DIR" "$WORK_DIR" "$ROOTFS_DIR" "$MUSL_LIBS_DIR"

# 1. Download Pinned Version Tarballs
echo -e "\n[+] 1/11 Downloading pinned version components..."
download_tarball() {
    local url=$1
    local dest=$2
    if [ ! -f "$dest" ]; then
        echo "  - Downloading $url"
        wget -c "$url" -O "$dest"
    else
        echo "  - Cached: $(basename "$dest")"
    fi
}

download_tarball "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-$LINUX_VERSION.tar.xz" "$DOWNLOAD_DIR/linux-$LINUX_VERSION.tar.xz"
download_tarball "https://busybox.net/downloads/busybox-$BUSYBOX_VERSION.tar.bz2" "$DOWNLOAD_DIR/busybox-$BUSYBOX_VERSION.tar.bz2"
download_tarball "https://skarnet.org/software/skalibs/skalibs-$SKALIBS_VERSION.tar.gz" "$DOWNLOAD_DIR/skalibs-$SKALIBS_VERSION.tar.gz"
download_tarball "https://skarnet.org/software/execline/execline-$EXECLINE_VERSION.tar.gz" "$DOWNLOAD_DIR/execline-$EXECLINE_VERSION.tar.gz"
download_tarball "https://skarnet.org/software/s6/s6-$S6_VERSION.tar.gz" "$DOWNLOAD_DIR/s6-$S6_VERSION.tar.gz"

# 2. Extract Pinned Version Tarballs
echo -e "\n[+] 2/11 Extracting source tarballs..."
extract_tarball() {
    local tarball=$1
    local name=$2
    local flags=$3
    if [ ! -d "$SRC_DIR/$name" ]; then
        echo "  - Extracting $(basename "$tarball")..."
        tar $flags "$tarball" -C "$SRC_DIR"
    else
        echo "  - Directory exists: $name"
    fi
}

extract_tarball "$DOWNLOAD_DIR/linux-$LINUX_VERSION.tar.xz" "linux-$LINUX_VERSION" "-xf"
extract_tarball "$DOWNLOAD_DIR/busybox-$BUSYBOX_VERSION.tar.bz2" "busybox-$BUSYBOX_VERSION" "-xf"
extract_tarball "$DOWNLOAD_DIR/skalibs-$SKALIBS_VERSION.tar.gz" "skalibs-$SKALIBS_VERSION" "-xf"
extract_tarball "$DOWNLOAD_DIR/execline-$EXECLINE_VERSION.tar.gz" "execline-$EXECLINE_VERSION" "-xf"
extract_tarball "$DOWNLOAD_DIR/s6-$S6_VERSION.tar.gz" "s6-$S6_VERSION" "-xf"

# 3. Compile s6 and dependencies against musl
echo -e "\n[+] 3/11 Compiling s6 process supervision suite against musl..."

# Compile skalibs
echo "  - Compiling skalibs..."
cd "$SRC_DIR/skalibs-$SKALIBS_VERSION"
CC=musl-gcc ./configure --prefix=/usr --includedir=/usr/include/x86_64-linux-musl --libdir=/usr/lib/x86_64-linux-musl --enable-static --disable-shared
make clean
make -j"$(nproc)"
make install

# Compile execline
echo "  - Compiling execline..."
cd "$SRC_DIR/execline-$EXECLINE_VERSION"
CC=musl-gcc ./configure --prefix=/usr --includedir=/usr/include/x86_64-linux-musl --libdir=/usr/lib/x86_64-linux-musl --enable-static --disable-shared
make clean
make -j"$(nproc)"
make install
make install DESTDIR="/tmp/s6_install"

# Compile s6
echo "  - Compiling s6 supervision..."
cd "$SRC_DIR/s6-$S6_VERSION"
CC="musl-gcc -include skalibs/cspawn.h -Duint16=uint16_t -Duint32=uint32_t -Duint64=uint64_t" ./configure --prefix=/usr --includedir=/usr/include/x86_64-linux-musl --libdir=/usr/lib/x86_64-linux-musl --enable-static --disable-shared
make clean
make -j"$(nproc)"
make install DESTDIR="/tmp/s6_install"

echo "[✔] Done: Static s6 binaries compiled successfully"


# 4. Configure and Compile static BusyBox using musl-gcc
echo -e "\n[+] 4/11 Configuring and compiling static BusyBox using musl..."
cd "$SRC_DIR/busybox-$BUSYBOX_VERSION"
make clean
make defconfig
# Ensure static binary build and disable tc utility
"$SRC_DIR/linux-$LINUX_VERSION/scripts/config" --file .config --enable CONFIG_STATIC
"$SRC_DIR/linux-$LINUX_VERSION/scripts/config" --file .config --disable CONFIG_TC
yes "" | make CC="musl-gcc -idirafter /usr/include -idirafter /usr/include/x86_64-linux-gnu" oldconfig
make CC="musl-gcc -idirafter /usr/include -idirafter /usr/include/x86_64-linux-gnu" -j"$(nproc)"
make CC="musl-gcc -idirafter /usr/include -idirafter /usr/include/x86_64-linux-gnu" install
echo "[✔] Done: BusyBox built statically in _install"

# 5. Build custom Linux Kernel with Btrfs support
echo -e "\n[+] 5/11 Configuring and compiling custom Linux kernel with Btrfs..."
cd "$SRC_DIR/linux-$LINUX_VERSION"
make defconfig

# Enable required configurations
scripts/config --enable CONFIG_BPF
scripts/config --enable CONFIG_KPROBES
scripts/config --enable CONFIG_FTRACE
scripts/config --enable CONFIG_FAULT_INJECTION
scripts/config --enable CONFIG_PERF_EVENTS
scripts/config --enable CONFIG_KALLSYMS
scripts/config --enable CONFIG_KASAN
scripts/config --enable CONFIG_DEBUG_FS
scripts/config --enable CONFIG_DEVTMPFS
scripts/config --enable CONFIG_DEVTMPFS_MOUNT
scripts/config --disable CONFIG_PANIC_ON_OOPS
scripts/config --disable CONFIG_BUG
scripts/config --enable CONFIG_DEBUG_KERNEL

# Enable Btrfs filesystem support
scripts/config --enable CONFIG_BTRFS_FS

make -j"$(nproc)"
echo "[✔] Done: kernel bzImage compiled"

# 6. Build out-of-tree Kernel Driver Module
echo -e "\n[+] 6/11 Compiling out-of-tree pron_mf_core kernel module..."
cd /workspace
make -C "$SRC_DIR/linux-$LINUX_VERSION" M=/workspace/kernel modules
echo "[✔] Done: kernel/pron_mf_core.ko built successfully"

# 7. Build C/C++ SDK (Shared Library) & tests against musl
echo -e "\n[+] 7/11 Compiling SDK and verification tests against musl..."
# Compile dynamic C SDK shared library
musl-gcc -O2 -fPIC -shared -Wl,-soname,libpronmemf.so -I/workspace/sdk/c/include /workspace/sdk/c/src/pmf.c -o /workspace/sdk/c/src/libpronmemf.so
# Compile C verification tests
musl-gcc -O2 -I/workspace/sdk/c/include /workspace/test/test_alloc.c -L/workspace/sdk/c/src -lpronmemf -Wl,-rpath,/usr/lib -o /workspace/test/test_alloc
# Compile C++ bounds test
zig c++ -target x86_64-linux-musl -O2 -I/workspace/sdk/c/include -I/workspace/sdk/cpp/include /workspace/test/test_bounds.cpp -L/workspace/sdk/c/src -lpronmemf -Wl,-rpath,/usr/lib -o /workspace/test/test_bounds
# Compile Zig verification test
cp /workspace/sdk/zig/pronmf.zig /workspace/test/pronmf.zig
cd /workspace/test && zig build-exe -target x86_64-linux-musl -O ReleaseSafe /workspace/test/test_zig.zig -lc -L/workspace/sdk/c/src -lpronmemf -I/workspace/sdk/c/include
# Compile Rust verification test
cd /workspace/test/test_rust && cargo build --target x86_64-unknown-linux-musl --release
echo "[✔] Done: SDK & tests compiled"

# 8. Compile Rust Runtime Daemon against musl target
echo -e "\n[+] 8/11 Compiling Rust Runtime Daemon targeting static musl..."
cd /workspace/daemon
cargo build --target x86_64-unknown-linux-musl --release
echo "[✔] Done: daemon built statically targeting x86_64-unknown-linux-musl"

# 9. Assemble Root Filesystem layout (Btrfs rootfs)
echo -e "\n[+] 9/11 Assembling rootfs tree structure..."
rm -rf "$ROOTFS_DIR"/*
mkdir -p "$ROOTFS_DIR"/{bin,sbin,usr/bin,usr/sbin,lib,lib64,proc,sys,dev,tmp,etc/s6-services/pronmfd,kernel,runtime/profiles,usr/lib}

# Copy BusyBox files
cp -av "$SRC_DIR/busybox-$BUSYBOX_VERSION/_install"/* "$ROOTFS_DIR"/

# Copy compiled s6 utilities
cp -av /tmp/s6_install/usr/bin/* "$ROOTFS_DIR/usr/bin/"
cp -av /tmp/s6_install/usr/sbin/* "$ROOTFS_DIR/usr/sbin/" 2>/dev/null || true


# Copy s6 service run script
cp -av /workspace/s6/pronmfd/run "$ROOTFS_DIR/etc/s6-services/pronmfd/run"
chmod +x "$ROOTFS_DIR/etc/s6-services/pronmfd/run"

# Copy daemon, SDK, tests, and default profiles
cp -av /workspace/daemon/target/x86_64-unknown-linux-musl/release/pronmftd "$ROOTFS_DIR/usr/bin/"
cp -av /workspace/sdk/c/src/libpronmemf.so "$ROOTFS_DIR/usr/lib/"
cp -av /workspace/test/test_alloc "$ROOTFS_DIR/usr/bin/"
cp -av /workspace/test/test_bounds "$ROOTFS_DIR/usr/bin/"
cp -av /workspace/test/test_zig "$ROOTFS_DIR/usr/bin/"
cp -av /workspace/test/test_rust/target/x86_64-unknown-linux-musl/release/test_rust "$ROOTFS_DIR/usr/bin/"
cp -av /workspace/profiles/default.mfs "$ROOTFS_DIR/runtime/profiles/"
cp -av /workspace/kernel/pron_mf_core.ko "$ROOTFS_DIR/kernel/"

# Copy musl dynamic linker and standard library
cp -av /usr/lib/x86_64-linux-musl/libc.so "$ROOTFS_DIR/lib/libc.so"
ln -sf libc.so "$ROOTFS_DIR/lib/ld-musl-x86_64.so.1"

# Extract dynamic library dependencies of compiled binaries and copy them
copy_deps() {
    local binary=$1
    local target_dir=$2
    ldd "$binary" | grep -o '/lib[^ ]*' | while read -r lib; do
        if [ -f "$lib" ]; then
            local dest="$target_dir/$(dirname "$lib")"
            mkdir -p "$dest"
            cp -L "$lib" "$dest/"
        fi
    done
}

echo "[+] Skipping host dynamic library dependencies copy (using pure musl compile)..."
# copy_deps /workspace/test/test_bounds "$ROOTFS_DIR"

# Write s6-driven /init boot script
cat <<'EOF' > "$ROOTFS_DIR/init"
#!/bin/sh

# Export system search path
export PATH=/usr/bin:/usr/sbin:/bin:/sbin

# Mount filesystem nodes
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev || mdev -s

echo "=========================================================="
echo "          Welcome to Pron (s6 Supervision)          "
echo "=========================================================="
echo "[+] System Boot Successful. Root filesystem type: Btrfs"

# Load Pron MF Core kernel module
if [ -f /kernel/pron_mf_core.ko ]; then
    echo "[+] Loading Pron MF Core kernel module..."
    insmod /kernel/pron_mf_core.ko
    mknod /dev/pron_mf c 240 0
    chmod 666 /dev/pron_mf
    echo "[✔] Registered device node /dev/pron_mf (major: 240)"
fi

# Launch s6 supervisor scans to monitor daemon
echo "[+] Initiating s6 process supervision..."
/usr/bin/s6-svscan /etc/s6-services &
S6_PID=$!

sleep 1.5

# Run validation checks via SDK allocator test
echo "[+] Running verification test (C Allocator)..."
LD_LIBRARY_PATH=/usr/lib /usr/bin/test_alloc

echo -e "\n[+] Running verification test (C++ Bounds Checks)..."
LD_LIBRARY_PATH=/usr/lib /usr/bin/test_bounds

echo -e "\n[+] Running verification test (Zig SDK FFI)..."
LD_LIBRARY_PATH=/usr/lib /usr/bin/test_zig

echo -e "\n[+] Running verification test (Rust SDK FFI)..."
LD_LIBRARY_PATH=/usr/lib /usr/bin/test_rust

# Spawn shell
echo -e "\nSpawning system terminal shell..."
exec /bin/sh
EOF

chmod +x "$ROOTFS_DIR/init"

# 10. Generate Btrfs Root Partition Image
echo -e "\n[+] 10/11 Packaging rootfs folder into Btrfs filesystem partition..."
dd if=/dev/zero of="$WORK_DIR/rootfs.img" bs=1M count=256
mkfs.btrfs -f -r "$ROOTFS_DIR" "$WORK_DIR/rootfs.img"
echo "[✔] Done: rootfs.img formatted as Btrfs and populated"

# 11. Create EFI System Partition (ESP) formatted as FAT32
echo -e "\n[+] 11/11 Constructing EFI System Partition (systemd-boot)..."
dd if=/dev/zero of="$WORK_DIR/esp.img" bs=1M count=64
mkfs.vfat -F 32 "$WORK_DIR/esp.img"

# Layout systemd-boot files
mkdir -p "$WORK_DIR/loader/entries"

cat <<EOF > "$WORK_DIR/loader/loader.conf"
default pron
timeout 3
EOF

cat <<EOF > "$WORK_DIR/loader/entries/pron.conf"
title Pron (Serial Console)
linux /vmlinuz
options root=/dev/sda2 rootfstype=btrfs console=tty0 console=ttyS0 init=/init rw
EOF

cat <<EOF > "$WORK_DIR/loader/entries/pron-gui.conf"
title Pron (VGA Graphics)
linux /vmlinuz
options root=/dev/sda2 rootfstype=btrfs console=ttyS0 console=tty0 init=/init rw
EOF

# Copy loader files to FAT32 ESP
mmd -i "$WORK_DIR/esp.img" ::/EFI
mmd -i "$WORK_DIR/esp.img" ::/EFI/BOOT
mmd -i "$WORK_DIR/esp.img" ::/loader
mmd -i "$WORK_DIR/esp.img" ::/loader/entries

# Copy systemd-boot x86_64 efi bootloader
mcopy -i "$WORK_DIR/esp.img" /usr/lib/systemd/boot/efi/systemd-bootx64.efi ::/EFI/BOOT/BOOTX64.EFI
# Copy Kernel vmlinuz (bzImage)
mcopy -i "$WORK_DIR/esp.img" "$SRC_DIR/linux-$LINUX_VERSION/arch/x86/boot/bzImage" ::/vmlinuz
# Copy configuration files
mcopy -i "$WORK_DIR/esp.img" "$WORK_DIR/loader/loader.conf" ::/loader/loader.conf
mcopy -i "$WORK_DIR/esp.img" "$WORK_DIR/loader/entries/pron.conf" ::/loader/entries/pron.conf
mcopy -i "$WORK_DIR/esp.img" "$WORK_DIR/loader/entries/pron-gui.conf" ::/loader/entries/pron-gui.conf


# 12. Create Master UEFI GPT Disk Image
echo -e "\n[+] Assembling master UEFI GPT disk image..."
dd if=/dev/zero of="$WORK_DIR/pron.img" bs=1M count=325

# Setup Partition maps
sfdisk "$WORK_DIR/pron.img" <<EOF
label: gpt
part1 : start=2048, size=131072, type=c12a7328-f81f-11d2-ba4b-00a0c93ec93b, name="ESP"
part2 : start=133120, size=524288, type=0fc63daf-8483-4772-8e79-3d69d8477de4, name="Root"
EOF

# Write partitions
dd if="$WORK_DIR/esp.img" of="$WORK_DIR/pron.img" bs=1M seek=1 conv=notrunc
dd if="$WORK_DIR/rootfs.img" of="$WORK_DIR/pron.img" bs=1M seek=65 conv=notrunc

# Export Image
cp "$WORK_DIR/pron.img" "$OUTPUT_DIR/pron.img"

echo "=========================================================="
echo "     Pron UEFI Btrfs Image Generated Successfully!        "
echo "     Image path: $OUTPUT_DIR/pron.img                     "
echo "=========================================================="
