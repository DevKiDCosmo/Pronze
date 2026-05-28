from __future__ import annotations

import os
from typing import override

from common import PipelineNode, PipelineContext, run_cmd


class PackageBtrfsImageStage(PipelineNode):
    def __init__(self) -> None:
        super().__init__("PackageBtrfsImage", ["AssembleRootfs"])

    @override
    def run(self, context: PipelineContext) -> None:
        rootfs_img = os.path.join(context.work_dir, "rootfs.img")
        _ = run_cmd(f"dd if=/dev/zero of={rootfs_img} bs=1M count=256")
        _ = run_cmd(f"mkfs.btrfs -f -b 268435456 -r {context.rootfs_dir} {rootfs_img}")
