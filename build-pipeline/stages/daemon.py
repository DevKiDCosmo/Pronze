import os
import shutil
from common import PipelineNode, Logger, run_cmd, update_node_status, get_dir_hash

class CompileDaemonStage(PipelineNode):
    def __init__(self):
        super().__init__("CompileDaemon", ["CheckEarlyExit"])

    def run(self, context):
        daemon_hash = get_dir_hash(os.path.join(context.workspace_dir, "daemon"))
        saved_hash_file = os.path.join(context.nochanges_dir, "CompileDaemon.hash")
        cached_daemon = os.path.join(context.nochanges_dir, "pronzed")
        target_daemon = os.path.join(context.workspace_dir, "daemon/target/x86_64-unknown-linux-musl/release/pronzed")
        done_flag_file = os.path.join(context.nochanges_dir, "CompileDaemon.done")

        saved_hash = ""
        if os.path.exists(saved_hash_file):
            with open(saved_hash_file, 'r') as f:
                saved_hash = f.read().strip()

        if saved_hash == daemon_hash and os.path.exists(done_flag_file) and os.path.exists(cached_daemon):
            Logger.log_success("Restoring pronzed daemon from cache...")
            os.makedirs(os.path.dirname(target_daemon), exist_ok=True)
            shutil.copy2(cached_daemon, target_daemon)
            shutil.copy2(cached_daemon, context.output_dir)
            update_node_status("CompileDaemon", "Skipped", "Restored from cache")
            
            with open(done_flag_file, "w") as f:
                f.write("OK\n")
            return

        # Start compilation: clean up flags
        if os.path.exists(done_flag_file):
            os.remove(done_flag_file)
        if os.path.exists(saved_hash_file):
            os.remove(saved_hash_file)

        Logger.log_step("Compiling Rust Runtime Daemon targeting static musl...")
        run_cmd("cargo build --target x86_64-unknown-linux-musl --release", cwd=os.path.join(context.workspace_dir, "daemon"))
        
        if not os.path.exists(target_daemon):
            raise Exception("Failed to compile pronzed daemon")

        shutil.copy2(target_daemon, cached_daemon)
        shutil.copy2(cached_daemon, context.output_dir)
        
        with open(saved_hash_file, "w") as f:
            f.write(daemon_hash + "\n")
            
        with open(done_flag_file, "w") as f:
            f.write("OK\n")
