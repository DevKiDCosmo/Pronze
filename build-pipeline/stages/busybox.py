import os
import hashlib
from common import PipelineNode, Logger, run_cmd, update_node_status, get_file_hash

class CompileBusyboxStage(PipelineNode):
    def __init__(self):
        super().__init__("CompileBusybox", ["CompileKernel"])

    def run(self, context):
        busybox_ver = context.config['BUSYBOX_VERSION']
        busybox_tar_hash = get_file_hash(os.path.join(context.download_dir, f"busybox-{busybox_ver}.tar.bz2"))
        busybox_hash = hashlib.sha256(f"{busybox_ver}-{busybox_tar_hash}".encode('utf-8')).hexdigest()
        
        saved_hash_file = os.path.join(context.nochanges_dir, "CompileBusybox.hash")
        cached_tar = os.path.join(context.nochanges_dir, "busybox_install.tar.gz")
        done_flag_file = os.path.join(context.nochanges_dir, "CompileBusybox.done")

        saved_hash = ""
        if os.path.exists(saved_hash_file):
            with open(saved_hash_file, 'r') as f:
                saved_hash = f.read().strip()

        if saved_hash == busybox_hash and os.path.exists(done_flag_file) and os.path.exists(cached_tar):
            Logger.log_success("Restoring BusyBox from cache...")
            run_cmd(f"tar -xzf {cached_tar} -C {os.path.join(context.src_dir, f'busybox-{busybox_ver}')}")
            update_node_status("CompileBusybox", "Skipped", "Restored from cache")
            
            with open(done_flag_file, "w") as f:
                f.write("OK\n")
            return

        # Start compilation: clean up flags
        if os.path.exists(done_flag_file):
            os.remove(done_flag_file)
        if os.path.exists(saved_hash_file):
            os.remove(saved_hash_file)

        bb_dir = os.path.join(context.src_dir, f"busybox-{busybox_ver}")
        linux_dir = os.path.join(context.src_dir, f"linux-{context.config['LINUX_VERSION']}")

        Logger.log_step("Configuring and building static BusyBox...")
        musl_inc = '-idirafter /usr/include -idirafter /usr/include/x86_64-linux-gnu'
        run_cmd(f'make CC="musl-gcc {musl_inc}" defconfig', cwd=bb_dir)
        run_cmd(f'"{linux_dir}/scripts/config" --file .config --enable CONFIG_STATIC', cwd=bb_dir)
        run_cmd(f'"{linux_dir}/scripts/config" --file .config --disable CONFIG_TC', cwd=bb_dir)
        run_cmd(f'yes "" | make CC="musl-gcc {musl_inc}" oldconfig', cwd=bb_dir)
        run_cmd(f'make CC="musl-gcc {musl_inc}" -j$(nproc)', cwd=bb_dir)
        run_cmd(f'make CC="musl-gcc {musl_inc}" install', cwd=bb_dir)

        # Cache
        Logger.log_info("Archiving BusyBox to cache...")
        run_cmd(f"tar -czf {cached_tar} -C {bb_dir} _install")
        
        with open(saved_hash_file, "w") as f:
            f.write(busybox_hash + "\n")
            
        with open(done_flag_file, "w") as f:
            f.write("OK\n")
