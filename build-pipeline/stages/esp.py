from __future__ import annotations

import os
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from common import PipelineNode, PipelineContext, run_cmd, write_text_file


class PackageESPImageStage(PipelineNode):
    def __init__(self) -> None:
        super().__init__("PackageESPImage", ["CompileKernel"])

    @override
    def run(self, context: PipelineContext) -> None:
        esp_img = os.path.join(context.work_dir, "esp.img")
        _ = run_cmd(f"dd if=/dev/zero of={esp_img} bs=1M count=64")
        _ = run_cmd(f"mkfs.vfat -F 32 {esp_img}")

        loader_dir = os.path.join(context.work_dir, "loader")
        os.makedirs(os.path.join(loader_dir, "entries"), exist_ok=True)
        write_text_file(os.path.join(loader_dir, "loader.conf"), "default pronze\ntimeout 3\n")
        write_text_file(os.path.join(loader_dir, "entries/pronze.conf"), "title Pronze (Serial Console)\nlinux /vmlinuz\noptions root=/dev/sda2 rootfstype=btrfs console=tty0 console=ttyS0 init=/init rw\n")
        write_text_file(os.path.join(loader_dir, "entries/pronze-gui.conf"), "title Pronze (VGA Graphics)\nlinux /vmlinuz\noptions root=/dev/sda2 rootfstype=btrfs console=ttyS0 console=tty0 init=/init rw\n")

        for path in ["::/EFI", "::/EFI/BOOT", "::/loader", "::/loader/entries"]:
            _ = run_cmd(f"mmd -i {esp_img} {path}")

        linux_ver = context.config['LINUX_VERSION']
        _ = run_cmd(f"mcopy -i {esp_img} /usr/lib/systemd/boot/efi/systemd-bootx64.efi ::/EFI/BOOT/BOOTX64.EFI")
        _ = run_cmd(f"mcopy -i {esp_img} {os.path.join(context.src_dir, f'linux-{linux_ver}/arch/x86/boot/bzImage')} ::/vmlinuz")
        _ = run_cmd(f"mcopy -i {esp_img} {os.path.join(loader_dir, 'loader.conf')} ::/loader/loader.conf")
        _ = run_cmd(f"mcopy -i {esp_img} {os.path.join(loader_dir, 'entries/pronze.conf')} ::/loader/entries/pronze.conf")
        _ = run_cmd(f"mcopy -i {esp_img} {os.path.join(loader_dir, 'entries/pronze-gui.conf')} ::/loader/entries/pronze-gui.conf")
