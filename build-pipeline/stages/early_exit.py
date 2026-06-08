import os
import shutil
import hashlib
from common import PipelineNode, Logger, update_node_status, compute_all_hashes

class CheckEarlyExitStage(PipelineNode):
    def __init__(self):
        super().__init__("CheckEarlyExit", ["DownloadTarballs"])

    def run(self, context):
        current_hashes = compute_all_hashes(context)
        inputs = [
            current_hashes["kernel_dir"],
            current_hashes["sdk_dir"],
            current_hashes["daemon_dir"],
            current_hashes["s6_dir"],
            current_hashes["test_dir"],
            current_hashes["profiles_dir"],
            current_hashes["build_pipeline_dir"],
            current_hashes.get("config_setup_dir", ""),
            current_hashes["pipeline_conf"],
            current_hashes["linux_ver"],
            current_hashes["busybox_ver"],
            current_hashes["skalibs_ver"],
            current_hashes["execline_ver"],
            current_hashes["s6_ver"],
            current_hashes["linux_tar"],
            current_hashes["busybox_tar"],
            current_hashes["skalibs_tar"],
            current_hashes["execline_tar"],
            current_hashes["s6_tar"],
        ]
        master_string = "-".join(inputs)
        context.master_hash = hashlib.sha256(master_string.encode('utf-8')).hexdigest()
        
        saved_hash_file = os.path.join(context.nochanges_dir, "master.hash")
        cached_img = os.path.join(context.nochanges_dir, "pronzeos.img")
        output_img = os.path.join(context.output_dir, "pronzeos.img")
        done_flag_file = os.path.join(context.nochanges_dir, "master.done")

        saved_hash = ""
        if os.path.exists(saved_hash_file):
            with open(saved_hash_file, 'r') as f:
                saved_hash = f.read().strip()

        repackage_active = getattr(context, "repackage_only_active", False)
        if not repackage_active and saved_hash == context.master_hash and os.path.exists(done_flag_file):
            if os.path.exists(output_img):
                Logger.log_success("Already compiled! Entire build pipeline skipped.")
                context.skip_remaining = True
                update_node_status("CheckEarlyExit", "Success", "Unchanged (Early Exit)")
                
                # Also mark CheckEarlyExit stage as done
                stage_done = os.path.join(context.nochanges_dir, "CheckEarlyExit.done")
                with open(stage_done, "w") as f:
                    f.write("OK\n")
                return
            elif os.path.exists(cached_img):
                Logger.log_success("Restoring complete PronzeOS image and binaries from cache...")
                shutil.copy2(cached_img, output_img)
                
                # Restore binaries
                binaries = ["libpronze.so", "pronzed", "test_alloc", "test_bounds", "test_zig", "test_rust", "pronze.ko"]
                for b in binaries:
                    src = os.path.join(context.nochanges_dir, b)
                    if os.path.exists(src):
                        shutil.copy2(src, context.output_dir)
                
                # Restore workspace locations
                for src_name, dest_rel in [
                    ("libpronze.so", "sdk/c/src/libpronze.so"),
                    ("pronze.ko", "kernel/pronze.ko"),
                    ("test_alloc", "test/test_alloc"),
                    ("test_bounds", "test/test_bounds"),
                    ("test_zig", "test/test_zig"),
                ]:
                    src_path = os.path.join(context.nochanges_dir, src_name)
                    if os.path.exists(src_path):
                        shutil.copy2(src_path, os.path.join(context.workspace_dir, dest_rel))
                
                rust_target_dir = os.path.join(context.workspace_dir, "test/test_rust/target/x86_64-unknown-linux-musl/release")
                daemon_target_dir = os.path.join(context.workspace_dir, "daemon/target/x86_64-unknown-linux-musl/release")
                os.makedirs(rust_target_dir, exist_ok=True)
                os.makedirs(daemon_target_dir, exist_ok=True)
                
                cached_rust = os.path.join(context.nochanges_dir, "test_rust")
                if os.path.exists(cached_rust):
                    shutil.copy2(cached_rust, os.path.join(rust_target_dir, "test_rust"))
                
                cached_daemon = os.path.join(context.nochanges_dir, "pronzed")
                if os.path.exists(cached_daemon):
                    shutil.copy2(cached_daemon, os.path.join(daemon_target_dir, "pronzed"))

                context.skip_remaining = True
                update_node_status("CheckEarlyExit", "Success", "Restored from Cache")
                
                stage_done = os.path.join(context.nochanges_dir, "CheckEarlyExit.done")
                with open(stage_done, "w") as f:
                    f.write("OK\n")
                return

        update_node_status("CheckEarlyExit", "Success", "Changes detected, executing DAG")
        stage_done = os.path.join(context.nochanges_dir, "CheckEarlyExit.done")
        with open(stage_done, "w") as f:
            f.write("OK\n")
