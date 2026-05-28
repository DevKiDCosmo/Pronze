import os
import shutil
import hashlib
from common import PipelineNode, Logger, run_cmd, update_node_status, get_file_hash

class CompileKernelStage(PipelineNode):
    def __init__(self):
        super().__init__("CompileKernel", ["ExtractTarballs"])

    def run(self, context):
        linux_ver = context.config['LINUX_VERSION']
        linux_tar_hash = get_file_hash(os.path.join(context.download_dir, f"linux-{linux_ver}.tar.xz"))
        kernel_hash = hashlib.sha256(f"{linux_ver}-{linux_tar_hash}".encode('utf-8')).hexdigest()

        saved_hash_file = os.path.join(context.nochanges_dir, "CompileKernel.hash")
        cached_bzimage = os.path.join(context.nochanges_dir, "bzImage")
        done_flag_file = os.path.join(context.nochanges_dir, "CompileKernel.done")

        saved_hash = ""
        if os.path.exists(saved_hash_file):
            with open(saved_hash_file, 'r') as f:
                saved_hash = f.read().strip()

        linux_dir = os.path.join(context.src_dir, f"linux-{linux_ver}")

        if saved_hash == kernel_hash and os.path.exists(done_flag_file) and os.path.exists(cached_bzimage):
            Logger.log_success("Restoring custom Linux kernel bzImage from cache...")
            os.makedirs(os.path.join(linux_dir, "arch/x86/boot"), exist_ok=True)
            shutil.copy2(cached_bzimage, os.path.join(linux_dir, "arch/x86/boot/bzImage"))
            update_node_status("CompileKernel", "Skipped", "Restored from cache")
            
            # Re-create done flag just in case
            with open(done_flag_file, "w") as f:
                f.write("OK\n")
            return

        # Start of compilation: ensure done flag and hash are removed (if they exist)
        if os.path.exists(done_flag_file):
            os.remove(done_flag_file)
        if os.path.exists(saved_hash_file):
            os.remove(saved_hash_file)

        Logger.log_step("Configuring and compiling custom Linux kernel...")
        run_cmd("make defconfig", cwd=linux_dir)
        configs_to_enable = [
            "CONFIG_BPF", "CONFIG_KPROBES", "CONFIG_FTRACE", 
            "CONFIG_FAULT_INJECTION", "CONFIG_PERF_EVENTS", 
            "CONFIG_KALLSYMS", "CONFIG_KASAN", "CONFIG_DEBUG_FS",
            "CONFIG_DEVTMPFS", "CONFIG_DEVTMPFS_MOUNT", 
            "CONFIG_DEBUG_KERNEL", "CONFIG_BTRFS_FS"
        ]
        configs_to_disable = ["CONFIG_PANIC_ON_OOPS", "CONFIG_BUG"]

        for cfg in configs_to_enable:
            run_cmd(f"scripts/config --enable {cfg}", cwd=linux_dir)
        for cfg in configs_to_disable:
            run_cmd(f"scripts/config --disable {cfg}", cwd=linux_dir)

        run_cmd("make olddefconfig", cwd=linux_dir)
        run_cmd("make -j$(nproc)", cwd=linux_dir)

        # Cache
        Logger.log_info("Archiving Linux kernel bzImage to cache...")
        shutil.copy2(os.path.join(linux_dir, "arch/x86/boot/bzImage"), cached_bzimage)
        
        with open(saved_hash_file, "w") as f:
            f.write(kernel_hash + "\n")
            
        with open(done_flag_file, "w") as f:
            f.write("OK\n")
