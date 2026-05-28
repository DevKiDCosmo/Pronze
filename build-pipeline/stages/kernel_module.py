import os
import shutil
from common import PipelineNode, Logger, run_cmd, update_node_status, get_dir_hash

class CompileKernelModuleStage(PipelineNode):
    def __init__(self):
        super().__init__("CompileKernelModule", ["CompileKernel"])

    def run(self, context):
        kernel_mod_hash = get_dir_hash(os.path.join(context.workspace_dir, "kernel"))
        saved_hash_file = os.path.join(context.nochanges_dir, "CompileKernelModule.hash")
        cached_ko = os.path.join(context.nochanges_dir, "pronze.ko")
        target_ko = os.path.join(context.workspace_dir, "kernel/pronze.ko")
        done_flag_file = os.path.join(context.nochanges_dir, "CompileKernelModule.done")

        saved_hash = ""
        if os.path.exists(saved_hash_file):
            with open(saved_hash_file, 'r') as f:
                saved_hash = f.read().strip()

        if saved_hash == kernel_mod_hash and os.path.exists(done_flag_file) and os.path.exists(cached_ko):
            Logger.log_success("Restoring pronze.ko from cache...")
            shutil.copy2(cached_ko, target_ko)
            shutil.copy2(cached_ko, context.output_dir)
            update_node_status("CompileKernelModule", "Skipped", "Restored from cache")
            
            with open(done_flag_file, "w") as f:
                f.write("OK\n")
            return

        # Start compilation: clean up flags
        if os.path.exists(done_flag_file):
            os.remove(done_flag_file)
        if os.path.exists(saved_hash_file):
            os.remove(saved_hash_file)

        # Git clone target kernel headers version or compile module against target source
        linux_ver = context.config['LINUX_VERSION']
        hdr_version = context.config.get('LINUX_HEADERS_VERSION', linux_ver)
        linux_dir = os.path.join(context.src_dir, f"linux-{hdr_version}")

        # Git download or clone if directory not present (fallback)
        if not os.path.exists(linux_dir):
            Logger.log_warn(f"Kernel headers target source {linux_dir} not found. Attempting checkout/tarball extraction...")
            hdr_git_success = False
            if shutil.which("git"):
                Logger.log_step(f"Attempting to clone Linux Kernel Headers v{hdr_version} using Git...")
                try:
                    run_cmd(f"git clone --depth 1 --branch v{hdr_version} https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git {linux_dir}")
                    Logger.log_success("Kernel headers cloned successfully using Git!")
                    hdr_git_success = True
                except Exception as e:
                    Logger.log_warn(f"Git clone failed: {e}. Falling back to downloading tarball...")
            else:
                Logger.log_warn("Git not found. Falling back to downloading tarball...")

            if not hdr_git_success:
                tarball = os.path.join(context.download_dir, f"linux-{hdr_version}.tar.xz")
                run_cmd(f"tar -xf {tarball} -C {context.src_dir}")

        # Configure and prepare headers if not done yet
        if not os.path.exists(os.path.join(linux_dir, ".config")):
            run_cmd("make defconfig", cwd=linux_dir)
        if not os.path.exists(os.path.join(linux_dir, "include/generated/utsrelease.h")):
            run_cmd("make modules_prepare -j$(nproc)", cwd=linux_dir)

        Logger.log_step("Compiling out-of-tree pronze kernel module...")
        run_cmd(f"make KBUILD_MODPOST_WARN=1 -C {linux_dir} M={os.path.join(context.workspace_dir, 'kernel')} modules")

        if not os.path.exists(target_ko):
            raise Exception("Failed to compile pronze.ko")

        shutil.copy2(target_ko, cached_ko)
        shutil.copy2(target_ko, context.output_dir)
        
        with open(saved_hash_file, "w") as f:
            f.write(kernel_mod_hash + "\n")
            
        with open(done_flag_file, "w") as f:
            f.write("OK\n")
