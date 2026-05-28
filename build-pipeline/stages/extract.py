import os
from common import PipelineNode, Logger, run_cmd

class ExtractTarballsStage(PipelineNode):
    def __init__(self):
        super().__init__("ExtractTarballs", ["CheckEarlyExit"])

    def run(self, context):
        linux_ver = context.config['LINUX_VERSION']
        busybox_ver = context.config['BUSYBOX_VERSION']
        skalibs_ver = context.config['SKALIBS_VERSION']
        execline_ver = context.config['EXECLINE_VERSION']
        s6_ver = context.config['S6_VERSION']

        def extract(tarball, subdir_name):
            target = os.path.join(context.src_dir, subdir_name)
            if os.path.isdir(target):
                Logger.log_info(f"Directory exists: {subdir_name}")
                return
            Logger.log_step(f"Extracting {os.path.basename(tarball)}...")
            run_cmd(f"tar -xf {tarball} -C {context.src_dir}")

        linux_src_dir = os.path.join(context.src_dir, f"linux-{linux_ver}")
        if not os.path.exists(linux_src_dir):
            extract(os.path.join(context.download_dir, f"linux-{linux_ver}.tar.xz"), f"linux-{linux_ver}")
            
        extract(os.path.join(context.download_dir, f"busybox-{busybox_ver}.tar.bz2"), f"busybox-{busybox_ver}")
        extract(os.path.join(context.download_dir, f"skalibs-{skalibs_ver}.tar.gz"), f"skalibs-{skalibs_ver}")
        extract(os.path.join(context.download_dir, f"execline-{execline_ver}.tar.gz"), f"execline-{execline_ver}")
        extract(os.path.join(context.download_dir, f"s6-{s6_ver}.tar.gz"), f"s6-{s6_ver}")

        hdr_version = context.config.get('LINUX_HEADERS_VERSION', linux_ver)
        if hdr_version != linux_ver:
            hdr_src_dir = os.path.join(context.src_dir, f"linux-{hdr_version}")
            if not os.path.exists(hdr_src_dir):
                extract(os.path.join(context.download_dir, f"linux-{hdr_version}.tar.xz"), f"linux-{hdr_version}")

        # Mark this stage as completed with a done flag
        done_flag_file = os.path.join(context.nochanges_dir, "ExtractTarballs.done")
        with open(done_flag_file, "w") as f:
            f.write("OK\n")
