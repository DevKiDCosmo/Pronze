from __future__ import annotations

import os
import shutil
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from common import PipelineNode, PipelineContext, copy_dir_contents, copy_file_or_symlink, Logger


class AssembleRootfsStage(PipelineNode):
    def __init__(self) -> None:
        super().__init__("AssembleRootfs", ["CompileS6", "CompileBusybox", "CompileKernelModule", "CompileSDK", "CompileDaemon"])

    @override
    def run(self, context: PipelineContext) -> None:
        if os.path.exists(context.rootfs_dir):
            shutil.rmtree(context.rootfs_dir)
        os.makedirs(context.rootfs_dir)

        for subdir in ["bin", "sbin", "usr/bin", "usr/sbin", "lib", "lib64", "proc", "sys", "dev", "tmp", "etc/s6-services/pronze", "kernel", "runtime/profiles", "usr/lib"]:
            os.makedirs(os.path.join(context.rootfs_dir, subdir), exist_ok=True)

        busybox_ver = context.config['BUSYBOX_VERSION']
        copy_dir_contents(os.path.join(context.src_dir, f"busybox-{busybox_ver}/_install"), context.rootfs_dir)

        for file_name in os.listdir("/tmp/s6_install/usr/bin"):
            copy_file_or_symlink(os.path.join("/tmp/s6_install/usr/bin", file_name), os.path.join(context.rootfs_dir, "usr/bin/"))

        run_script_src = os.path.join(context.workspace_dir, "s6/pronze/run")
        run_script_dest = os.path.join(context.rootfs_dir, "etc/s6-services/pronze/run")
        copy_file_or_symlink(run_script_src, run_script_dest)
        os.chmod(run_script_dest, 0o755)

        allowed_failures_raw = context.config.get("ALLOWED_FAILURES", "")
        allowed_failures = [x.strip() for x in allowed_failures_raw.split(",") if x.strip()]

        for src, dest in [
            ("daemon/target/x86_64-unknown-linux-musl/release/pronzed", "usr/bin/"),
            ("sdk/c/src/libpronze.so", "usr/lib/"),
            ("test/test_alloc", "usr/bin/"),
            ("test/test_bounds", "usr/bin/"),
            ("test/test_zig", "usr/bin/"),
            ("test/test_rust/target/x86_64-unknown-linux-musl/release/test_rust", "usr/bin/"),
            ("profiles/default.mfs", "runtime/profiles/"),
            ("kernel/pronze.ko", "kernel/"),
        ]:
            src_path = os.path.join(context.workspace_dir, src)
            if not os.path.exists(src_path):
                mapping = None
                if "daemon/" in src:
                    mapping = "CompileDaemon"
                elif "sdk/" in src or "test/test_" in src:
                    mapping = "CompileSDK"
                elif "kernel/" in src:
                    mapping = "CompileKernelModule"

                stage_failed = False
                if mapping:
                    from common import node_states
                    state = node_states.get(mapping, {})
                    if state.get("status") == "Failed" and mapping in allowed_failures:
                        stage_failed = True

                if stage_failed:
                    Logger.log_warn(f"Skipping copy of missing allowed-to-fail build target: {src}")
                    continue
                else:
                    raise FileNotFoundError(f"Required build target missing: {src_path}")

            copy_file_or_symlink(src_path, os.path.join(context.rootfs_dir, dest))

        copy_file_or_symlink("/usr/lib/x86_64-linux-musl/libc.so", os.path.join(context.rootfs_dir, "lib/libc.so"))
        ld_link = os.path.join(context.rootfs_dir, "lib/ld-musl-x86_64.so.1")
        if os.path.exists(ld_link) or os.path.islink(ld_link):
            os.remove(ld_link)
        os.symlink("libc.so", ld_link)

        init_script = """#!/bin/sh

export PATH=/usr/bin:/usr/sbin:/bin:/sbin

mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev || mdev -s

echo "=========================================================="
echo "          Welcome to Pronze (s6 Supervision)          "
echo "=========================================================="
echo "[+] System Boot Successful. Root filesystem type: Btrfs"

if [ -f /kernel/pronze.ko ]; then
    echo "[+] Loading Pronze kernel module..."
    insmod /kernel/pronze.ko
    mknod /dev/pronze c 240 0
    chmod 666 /dev/pronze
    echo "[✔] Registered device node /dev/pronze (major: 240)"
    mknod /dev/pronze_telemetry c 241 0
    chmod 666 /dev/pronze_telemetry
    echo "[✔] Registered device node /dev/pronze_telemetry (major: 241)"
fi

echo "[+] Initiating s6 process supervision..."
/usr/bin/s6-svscan /etc/s6-services &
S6_PID=$!

sleep 1.5

echo "[+] Running verification test (C Allocator)..."
LD_LIBRARY_PATH=/usr/lib /usr/bin/test_alloc

echo -e "\\n[+] Running verification test (C++ Bounds Checks)..."
LD_LIBRARY_PATH=/usr/lib /usr/bin/test_bounds

echo -e "\\n[+] Running verification test (Zig SDK FFI)..."
LD_LIBRARY_PATH=/usr/lib /usr/bin/test_zig

echo -e "\\n[+] Running verification test (Rust SDK FFI)..."
LD_LIBRARY_PATH=/usr/lib /usr/bin/test_rust

while true; do
    echo -e "\\nSpawning system terminal shell..."
    /bin/sh
    echo "Shell exited. Restarting shell..."
    sleep 1
done
"""
        init_dest = os.path.join(context.rootfs_dir, "init")
        with open(init_dest, "w", encoding="utf-8") as file_handle:
            _ = file_handle.write(init_script)
        os.chmod(init_dest, 0o755)
