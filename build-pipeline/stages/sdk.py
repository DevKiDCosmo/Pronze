import os
import shutil
import hashlib
from common import PipelineNode, Logger, run_cmd, update_node_status, get_dir_hash, get_file_hash

class CompileSDKStage(PipelineNode):
    def __init__(self):
        super().__init__("CompileSDK", ["CheckEarlyExit"])

    def run(self, context):
        sdk_dir_hash = get_dir_hash(os.path.join(context.workspace_dir, "sdk"))
        test_dir_hash = get_dir_hash(os.path.join(context.workspace_dir, "test"))
        conf_hash = get_file_hash(os.path.join(context.workspace_dir, "pipeline.conf"))
        sdk_combined_hash = hashlib.sha256(f"{sdk_dir_hash}-{test_dir_hash}-{conf_hash}".encode('utf-8')).hexdigest()

        saved_hash_file = os.path.join(context.nochanges_dir, "CompileSDK.hash")
        done_flag_file = os.path.join(context.nochanges_dir, "CompileSDK.done")
        
        cached_so = os.path.join(context.nochanges_dir, "libpronze.so")
        cached_alloc = os.path.join(context.nochanges_dir, "test_alloc")
        cached_bounds = os.path.join(context.nochanges_dir, "test_bounds")
        cached_zig = os.path.join(context.nochanges_dir, "test_zig")
        cached_rust = os.path.join(context.nochanges_dir, "test_rust")

        saved_hash = ""
        if os.path.exists(saved_hash_file):
            with open(saved_hash_file, 'r') as f:
                saved_hash = f.read().strip()

        # Verify all cached binaries are present
        cache_binaries_exist = (
            os.path.exists(cached_so) and 
            os.path.exists(cached_alloc) and 
            os.path.exists(cached_bounds) and 
            os.path.exists(cached_zig) and 
            os.path.exists(cached_rust)
        )

        if saved_hash == sdk_combined_hash and os.path.exists(done_flag_file) and cache_binaries_exist:
            Logger.log_success("Restoring SDK libraries and FFI tests from cache...")
            
            # Restore to workspace locations
            shutil.copy2(cached_so, os.path.join(context.workspace_dir, "sdk/c/src/libpronze.so"))
            shutil.copy2(cached_alloc, os.path.join(context.workspace_dir, "test/test_alloc"))
            shutil.copy2(cached_bounds, os.path.join(context.workspace_dir, "test/test_bounds"))
            shutil.copy2(cached_zig, os.path.join(context.workspace_dir, "test/test_zig"))
            
            rust_target_bin = os.path.join(context.workspace_dir, "test/test_rust/target/x86_64-unknown-linux-musl/release/test_rust")
            os.makedirs(os.path.dirname(rust_target_bin), exist_ok=True)
            shutil.copy2(cached_rust, rust_target_bin)

            # Restore to output directory
            shutil.copy2(cached_so, context.output_dir)
            shutil.copy2(cached_alloc, context.output_dir)
            shutil.copy2(cached_bounds, context.output_dir)
            shutil.copy2(cached_zig, context.output_dir)
            shutil.copy2(cached_rust, context.output_dir)

            update_node_status("CompileSDK", "Skipped", "Restored from cache")
            with open(done_flag_file, "w") as f:
                f.write("OK\n")
            return

        # Start compilation: clean up flags
        if os.path.exists(done_flag_file):
            os.remove(done_flag_file)
        if os.path.exists(saved_hash_file):
            os.remove(saved_hash_file)

        Logger.log_step("Compiling C SDK Shared Library & C/C++ tests against musl...")
        sdk_c_dir = os.path.join(context.workspace_dir, "sdk/c/src")
        run_cmd(f"musl-gcc -O2 -fPIC -shared -Wl,-soname,libpronze.so -I{os.path.join(context.workspace_dir, 'sdk/c/include')} {os.path.join(sdk_c_dir, 'pronze.c')} -o {os.path.join(sdk_c_dir, 'libpronze.so')}")
        run_cmd(f"musl-gcc -O2 -I{os.path.join(context.workspace_dir, 'sdk/c/include')} {os.path.join(context.workspace_dir, 'test/test_alloc.c')} -L{sdk_c_dir} -lpronze -Wl,-rpath,/usr/lib -o {os.path.join(context.workspace_dir, 'test/test_alloc')}")
        run_cmd(f"zig c++ -target x86_64-linux-musl -O2 -I{os.path.join(context.workspace_dir, 'sdk/c/include')} -I{os.path.join(context.workspace_dir, 'sdk/cpp/include')} {os.path.join(context.workspace_dir, 'test/test_bounds.cpp')} -L{sdk_c_dir} -lpronze -Wl,-rpath,/usr/lib -o {os.path.join(context.workspace_dir, 'test/test_bounds')}")
        
        Logger.log_step("Compiling Zig test against musl...")
        run_cmd("zig build-exe -target x86_64-linux-musl -O ReleaseSafe test_zig.zig", cwd=os.path.join(context.workspace_dir, "test"))
        
        Logger.log_step("Compiling Rust test targeting musl...")
        run_cmd("cargo build --target x86_64-unknown-linux-musl --release", cwd=os.path.join(context.workspace_dir, "test/test_rust"))

        # Cache compiled files
        rust_target_bin = os.path.join(context.workspace_dir, "test/test_rust/target/x86_64-unknown-linux-musl/release/test_rust")
        shutil.copy2(os.path.join(sdk_c_dir, "libpronze.so"), cached_so)
        shutil.copy2(os.path.join(context.workspace_dir, "test/test_alloc"), cached_alloc)
        shutil.copy2(os.path.join(context.workspace_dir, "test/test_bounds"), cached_bounds)
        shutil.copy2(os.path.join(context.workspace_dir, "test/test_zig"), cached_zig)
        shutil.copy2(rust_target_bin, cached_rust)

        # Copy to output directory
        shutil.copy2(cached_so, context.output_dir)
        shutil.copy2(cached_alloc, context.output_dir)
        shutil.copy2(cached_bounds, context.output_dir)
        shutil.copy2(cached_zig, context.output_dir)
        shutil.copy2(cached_rust, context.output_dir)

        with open(saved_hash_file, "w") as f:
            f.write(sdk_combined_hash + "\n")
            
        with open(done_flag_file, "w") as f:
            f.write("OK\n")
        update_node_status("CompileSDK", "Success", "SDK & Tests Complete")
