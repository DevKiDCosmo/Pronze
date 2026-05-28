import os
import shutil
import urllib.request
from common import PipelineNode, Logger, run_cmd

class DownloadTarballsStage(PipelineNode):
    def __init__(self):
        super().__init__("DownloadTarballs")

    def run(self, context):
        def download(url, dest):
            if os.path.exists(dest):
                Logger.log_info(f"Cached: {os.path.basename(dest)}")
                return
            Logger.log_step(f"Downloading {url}...")
            urllib.request.urlretrieve(url, dest)
            Logger.log_success(f"Downloaded {os.path.basename(dest)}")

        linux_ver = context.config['LINUX_VERSION']
        busybox_ver = context.config['BUSYBOX_VERSION']
        skalibs_ver = context.config['SKALIBS_VERSION']
        execline_ver = context.config['EXECLINE_VERSION']
        s6_ver = context.config['S6_VERSION']

        # Try git clone for linux kernel first
        linux_src_dir = os.path.join(context.src_dir, f"linux-{linux_ver}")
        git_success = False
        if not os.path.exists(linux_src_dir):
            if shutil.which("git"):
                Logger.log_step(f"Attempting to clone Linux Kernel v{linux_ver} using Git...")
                try:
                    run_cmd(f"git clone --depth 1 --branch v{linux_ver} https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git {linux_src_dir}")
                    Logger.log_success("Kernel cloned successfully using Git!")
                    git_success = True
                except Exception as e:
                    Logger.log_warn(f"Git clone failed: {e}. Falling back to downloading tarball...")
            else:
                Logger.log_warn("Git not found. Falling back to downloading tarball...")

        if not git_success and not os.path.exists(os.path.join(context.download_dir, f"linux-{linux_ver}.tar.xz")):
            download(f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{linux_ver}.tar.xz",
                     os.path.join(context.download_dir, f"linux-{linux_ver}.tar.xz"))

        download(f"https://busybox.net/downloads/busybox-{busybox_ver}.tar.bz2",
                 os.path.join(context.download_dir, f"busybox-{busybox_ver}.tar.bz2"))
        download(f"https://skarnet.org/software/skalibs/skalibs-{skalibs_ver}.tar.gz",
                 os.path.join(context.download_dir, f"skalibs-{skalibs_ver}.tar.gz"))
        download(f"https://skarnet.org/software/execline/execline-{execline_ver}.tar.gz",
                 os.path.join(context.download_dir, f"execline-{execline_ver}.tar.gz"))
        download(f"https://skarnet.org/software/s6/s6-{s6_ver}.tar.gz",
                 os.path.join(context.download_dir, f"s6-{s6_ver}.tar.gz"))

        # Download kernel module target headers version if different
        hdr_version = context.config.get('LINUX_HEADERS_VERSION', linux_ver)
        if hdr_version != linux_ver:
            hdr_src_dir = os.path.join(context.src_dir, f"linux-{hdr_version}")
            hdr_git_success = False
            if not os.path.exists(hdr_src_dir):
                if shutil.which("git"):
                    Logger.log_step(f"Attempting to clone Linux Kernel Headers v{hdr_version} using Git...")
                    try:
                        run_cmd(f"git clone --depth 1 --branch v{hdr_version} https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git {hdr_src_dir}")
                        Logger.log_success("Kernel headers cloned successfully using Git!")
                        hdr_git_success = True
                    except Exception as e:
                        Logger.log_warn(f"Git clone failed: {e}. Falling back to downloading tarball...")
                else:
                    Logger.log_warn("Git not found. Falling back to downloading tarball...")

            if not hdr_git_success and not os.path.exists(os.path.join(context.download_dir, f"linux-{hdr_version}.tar.xz")):
                download(f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{hdr_version}.tar.xz",
                         os.path.join(context.download_dir, f"linux-{hdr_version}.tar.xz"))

        # Mark this stage as completed with a done flag
        done_flag_file = os.path.join(context.nochanges_dir, "DownloadTarballs.done")
        with open(done_flag_file, "w") as f:
            f.write("OK\n")
