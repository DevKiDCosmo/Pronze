from __future__ import annotations

import os
import shutil
from typing import override

from common import PipelineNode, PipelineContext, Logger, update_node_status, compute_all_hashes, write_json_file, write_stage_flags


class ShipImageStage(PipelineNode):
    def __init__(self) -> None:
        super().__init__("ShipImage", ["AssembleGPTImage"])

    @override
    def run(self, context: PipelineContext) -> None:
        hash_file = os.path.join(context.nochanges_dir, "ShipImage.hash")
        done_file = os.path.join(context.nochanges_dir, "ShipImage.done")
        master_hash_file = os.path.join(context.nochanges_dir, "master.hash")
        master_done_file = os.path.join(context.nochanges_dir, "master.done")

        # 1. Check cache
        saved_hash = ""
        if os.path.exists(hash_file):
            with open(hash_file, 'r', encoding='utf-8') as f:
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

        if saved_hash == context.master_hash and os.path.exists(done_file):
            # Verify they all exist in nochanges_dir so we can restore them if missing in output_dir
            all_cached_exist = True
            for name in shipped_files:
                if not os.path.exists(os.path.join(context.nochanges_dir, name)):
                    all_cached_exist = False
                    break

            if all_cached_exist:
                Logger.log_success("Restoring shipped binaries and images from cache...")
                for name in shipped_files:
                    dest_path = os.path.join(context.output_dir, name)
                    src_path = os.path.join(context.nochanges_dir, name)
                    if os.path.exists(src_path):
                        shutil.copy2(src_path, dest_path)
                update_node_status("ShipImage", "Skipped", "Restored from cache")
                return

        # 2. Clear old flags to prevent dirty state if aborted
        for f in [hash_file, done_file, master_hash_file, master_done_file]:
            if os.path.exists(f):
                os.remove(f)

        Logger.log_step("Shipping final binaries and OS images...")
        master_img_src = os.path.join(context.work_dir, "pronzeos.img")
        master_img_dest = os.path.join(context.output_dir, "pronzeos.img")
        cached_img = os.path.join(context.nochanges_dir, "pronzeos.img")

        _ = shutil.copy2(master_img_src, master_img_dest)
        _ = shutil.copy2(master_img_dest, cached_img)

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
                _ = shutil.copy2(src_path, os.path.join(context.output_dir, name))
                _ = shutil.copy2(src_path, os.path.join(context.nochanges_dir, name))

        with open(master_hash_file, "w", encoding="utf-8") as file_handle:
            _ = file_handle.write(context.master_hash + "\n")
        with open(master_done_file, "w", encoding="utf-8") as file_handle:
            _ = file_handle.write("OK\n")

        write_json_file(os.path.join(context.nochanges_dir, "hashes.json"), compute_all_hashes(context))
        write_stage_flags(context, "master", context.master_hash)
        write_stage_flags(context, "ShipImage", context.master_hash)

