from __future__ import annotations

import os
import shutil
try:
    from typing import override
except ImportError:
    def override(func):
        return func

from common import PipelineNode, PipelineContext, Logger, update_node_status


class CleanOutputStage(PipelineNode):
    def __init__(self) -> None:
        super().__init__("CleanOutput", ["AssembleGPTImage"])

    @override
    def run(self, context: PipelineContext) -> None:
        # Check if ShipImage would be skipped
        hash_file = os.path.join(context.nochanges_dir, "ShipImage.hash")
        done_file = os.path.join(context.nochanges_dir, "ShipImage.done")
        saved_hash = ""
        if os.path.exists(hash_file):
            with open(hash_file, "r", encoding="utf-8") as f:
                saved_hash = f.read().strip()

        shipped_files = ["pronzeos.img"]
        for src, name in [
            ("sdk/c/src/libpronze.so", "libpronze.so"),
            ("daemon/target/x86_64-unknown-linux-musl/release/pronzed", "pronzed"),
            ("test/test_alloc", "test_alloc"),
            ("test/test_bounds", "test_bounds"),
            ("test/test_zig", "test_zig"),
            ("test/test_rust/target/x86_64-unknown-linux-musl/release/test_rust", "test_rust"),
            ("kernel/pronze.ko", "pronze.ko"),
        ]:
            src_path = os.path.join(context.workspace_dir, src)
            if os.path.exists(src_path):
                shipped_files.append(name)

        all_cached_exist = True
        for name in shipped_files:
            if not os.path.exists(os.path.join(context.nochanges_dir, name)):
                all_cached_exist = False
                break

        repackage_active = getattr(context, "repackage_only_active", False)

        if not repackage_active and saved_hash == context.master_hash and os.path.exists(done_file) and all_cached_exist:
            # Skip CleanOutput
            update_node_status("CleanOutput", "Skipped", "Output directory preserved (Ship cached)")
            return

        # Otherwise, run Clean Output!
        Logger.log_step(f"Cleaning output directory {context.output_dir}...")
        if os.path.isdir(context.output_dir):
            for item in os.listdir(context.output_dir):
                item_path = os.path.join(context.output_dir, item)
                try:
                    if os.path.isdir(item_path) and not os.path.islink(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                except Exception as e:
                    Logger.log_warn(f"Failed to delete {item_path} during CleanOutput: {e}")
        Logger.log_success("Output directory cleaned.")
        update_node_status("CleanOutput", "Success", "Output directory cleaned.")
