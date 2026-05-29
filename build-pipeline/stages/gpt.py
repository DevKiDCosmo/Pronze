from __future__ import annotations

import os
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from common import PipelineNode, PipelineContext, run_cmd


class AssembleGPTImageStage(PipelineNode):
    def __init__(self) -> None:
        super().__init__("AssembleGPTImage", ["PackageBtrfsImage", "PackageESPImage"])

    @override
    def run(self, context: PipelineContext) -> None:
        master_img = os.path.join(context.work_dir, "pronzeos.img")
        _ = run_cmd(f"dd if=/dev/zero of={master_img} bs=1M count=325")
        sfdisk_input = """label: gpt
part1 : start=2048, size=131072, type=c12a7328-f81f-11d2-ba4b-00a0c93ec93b, name=\"ESP\"
part2 : start=133120, size=524288, type=0fc63daf-8483-4772-8e79-3d69d8477de4, name=\"Root\"
"""
        _ = run_cmd(f"sfdisk {master_img}", input_data=sfdisk_input)
        esp_img = os.path.join(context.work_dir, "esp.img")
        rootfs_img = os.path.join(context.work_dir, "rootfs.img")
        _ = run_cmd(f"dd if={esp_img} of={master_img} bs=1M seek=1 conv=notrunc")
        _ = run_cmd(f"dd if={rootfs_img} of={master_img} bs=1M seek=65 conv=notrunc")
