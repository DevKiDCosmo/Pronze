import os
import hashlib
from common import PipelineNode, Logger, run_cmd, update_node_status, get_file_hash

class CompileS6Stage(PipelineNode):
    def __init__(self):
        super().__init__("CompileS6", ["CompileBusybox"])

    def run(self, context):
        skalibs_ver = context.config['SKALIBS_VERSION']
        execline_ver = context.config['EXECLINE_VERSION']
        s6_ver = context.config['S6_VERSION']

        skalibs_tar_hash = get_file_hash(os.path.join(context.download_dir, f"skalibs-{skalibs_ver}.tar.gz"))
        execline_tar_hash = get_file_hash(os.path.join(context.download_dir, f"execline-{execline_ver}.tar.gz"))
        s6_tar_hash = get_file_hash(os.path.join(context.download_dir, f"s6-{s6_ver}.tar.gz"))

        s6_hash = hashlib.sha256(f"{skalibs_ver}-{execline_ver}-{s6_ver}-{skalibs_tar_hash}-{execline_tar_hash}-{s6_tar_hash}".encode('utf-8')).hexdigest()
        saved_s6_hash_file = os.path.join(context.nochanges_dir, "CompileS6.hash")
        cached_s6_tar = os.path.join(context.nochanges_dir, "s6_install.tar.gz")
        done_flag_file = os.path.join(context.nochanges_dir, "CompileS6.done")

        saved_hash = ""
        if os.path.exists(saved_s6_hash_file):
            with open(saved_s6_hash_file, 'r') as f:
                saved_hash = f.read().strip()

        if saved_hash == s6_hash and os.path.exists(done_flag_file) and os.path.exists(cached_s6_tar):
            Logger.log_success("Restoring s6 process supervision from cache...")
            os.makedirs("/tmp/s6_install", exist_ok=True)
            run_cmd(f"tar -xzPf {cached_s6_tar}")
            update_node_status("CompileS6", "Skipped", "Restored from cache")
            
            with open(done_flag_file, "w") as f:
                f.write("OK\n")
            return

        # Start compilation: clean up flags
        if os.path.exists(done_flag_file):
            os.remove(done_flag_file)
        if os.path.exists(saved_s6_hash_file):
            os.remove(saved_s6_hash_file)

        # Compile skalibs
        Logger.log_step("Compiling skalibs...")
        run_cmd("CC=musl-gcc ./configure --prefix=/usr --includedir=/usr/include/x86_64-linux-musl --libdir=/usr/lib/x86_64-linux-musl --enable-static --disable-shared",
                cwd=os.path.join(context.src_dir, f"skalibs-{skalibs_ver}"))
        run_cmd("make clean && make -j$(nproc) && make install",
                cwd=os.path.join(context.src_dir, f"skalibs-{skalibs_ver}"))

        # Compile execline
        Logger.log_step("Compiling execline...")
        run_cmd("CC=musl-gcc ./configure --prefix=/usr --includedir=/usr/include/x86_64-linux-musl --libdir=/usr/lib/x86_64-linux-musl --enable-static --disable-shared",
                cwd=os.path.join(context.src_dir, f"execline-{execline_ver}"))
        run_cmd("make clean && make -j$(nproc) && make install && make install DESTDIR=/tmp/s6_install",
                cwd=os.path.join(context.src_dir, f"execline-{execline_ver}"))

        # Compile s6
        Logger.log_step("Compiling s6 supervision...")
        s6_env_cc = 'musl-gcc -include skalibs/cspawn.h -Duint16=uint16_t -Duint32=uint32_t -Duint64=uint64_t'
        run_cmd(f'CC="{s6_env_cc}" ./configure --prefix=/usr --includedir=/usr/include/x86_64-linux-musl --libdir=/usr/lib/x86_64-linux-musl --enable-static --disable-shared',
                cwd=os.path.join(context.src_dir, f"s6-{s6_ver}"))
        run_cmd("make clean && make -j$(nproc) && make install DESTDIR=/tmp/s6_install",
                cwd=os.path.join(context.src_dir, f"s6-{s6_ver}"))

        # Cache compiled s6
        Logger.log_info("Archiving s6 to cache...")
        run_cmd(f"tar -czPf {cached_s6_tar} /tmp/s6_install /usr/include/x86_64-linux-musl /usr/lib/x86_64-linux-musl")
        
        with open(saved_s6_hash_file, "w") as f:
            f.write(s6_hash + "\n")
            
        with open(done_flag_file, "w") as f:
            f.write("OK\n")
