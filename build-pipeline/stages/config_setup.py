from __future__ import annotations

import os
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from common import PipelineNode, PipelineContext, Logger, copy_dir_contents, copy_file_or_symlink


class CopyConfigurationSetupStage(PipelineNode):
    def __init__(self) -> None:
        super().__init__("CopyConfigurationSetup", ["AssembleRootfs"])

    @override
    def run(self, context: PipelineContext) -> None:
        src_dir = os.path.join(context.workspace_dir, "configuration_setup")
        if not os.path.isdir(src_dir):
            Logger.log_warn(f"configuration_setup directory not found at {src_dir}")
            return

        dest_dir = context.rootfs_dir
        Logger.log_step(f"Copying files from configuration_setup to rootfs (excluding README.md)...")

        for item in os.listdir(src_dir):
            if item == "README.md":
                continue
            s_path = os.path.join(src_dir, item)
            d_path = os.path.join(dest_dir, item)

            if os.path.isdir(s_path):
                os.makedirs(d_path, exist_ok=True)
                copy_dir_contents(s_path, d_path)
            else:
                copy_file_or_symlink(s_path, d_path)
        Logger.log_success("Configuration setup copied successfully.")
