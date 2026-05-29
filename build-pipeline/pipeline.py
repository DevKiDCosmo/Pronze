#!/usr/bin/env python3

import os
import sys
import time
import argparse
from common import (
    Logger,
    Pipeline,
    PipelineContext,
    set_runtime_flags,
    print_status_table,
    start_http_server
)
from stages import (
    DownloadTarballsStage,
    CheckEarlyExitStage,
    ExtractTarballsStage,
    CompileKernelStage,
    CompileBusyboxStage,
    CompileS6Stage,
    CompileKernelModuleStage,
    CompileSDKStage,
    CompileDaemonStage,
    AssembleRootfsStage,
    PackageBtrfsImageStage,
    PackageESPImageStage,
    AssembleGPTImageStage,
    ShipImageStage,
    CopyConfigurationSetupStage
)

def main():
    parser = argparse.ArgumentParser(description="PronzeOS DAG Build Pipeline")
    parser.add_argument("--target", choices=["distro", "module"], default="distro", help="Build target")
    parser.add_argument("--port", type=int, default=8000, help="Live web GUI server port")
    parser.add_argument("--no-view", action="store_true", help="Disable the HTTP server and GUI dashboard")
    parser.add_argument("--no-logs", action="store_true", help="Suppress verbose compilation output in webview")
    parser.add_argument("--no-logs-terminal", action="store_true", help="Suppress verbose compilation output in terminal console")
    args = parser.parse_args()

    set_runtime_flags(args.no_logs, args.no_logs_terminal, args.no_view)

    opt_dir = "/opt/pronkern" if os.path.isdir("/opt/pronkern") else "/opt/pronze"
    workspace_dir = "/workspace" if os.path.exists("/workspace") else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    context = PipelineContext(args.target, workspace_dir, opt_dir)
    print_status_table(context)

    pipeline = Pipeline()
    
    # Add stages in dependency order
    pipeline.add_node(DownloadTarballsStage())
    pipeline.add_node(CheckEarlyExitStage())
    pipeline.add_node(ExtractTarballsStage())
    pipeline.add_node(CompileKernelStage())
    pipeline.add_node(CompileBusyboxStage())
    pipeline.add_node(CompileS6Stage())
    pipeline.add_node(CompileKernelModuleStage())
    pipeline.add_node(CompileSDKStage())
    pipeline.add_node(CompileDaemonStage())
    pipeline.add_node(AssembleRootfsStage())
    pipeline.add_node(CopyConfigurationSetupStage())
    pipeline.add_node(PackageBtrfsImageStage())
    pipeline.add_node(PackageESPImageStage())
    pipeline.add_node(AssembleGPTImageStage())
    pipeline.add_node(ShipImageStage())

    pipeline.build_graph()

    if not args.no_view:
        import common
        common.global_pipeline = pipeline
        try:
            common.start_http_server(args.port)
        except Exception as e:
            Logger.log_warn(f"Failed to start http server: {e}")
        
        # Enqueue the first build run
        common.build_queue.put((context, pipeline))
        
        Logger.log_info(f"Web GUI is active at http://localhost:{args.port}")
        Logger.log_info("Press Ctrl+C to exit and stop the server.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            Logger.log_info("Stopping server and exiting.")
    else:
        # Run synchronously if no-view is specified
        pipeline.execute(context)
        Logger.log_success("Build finished!")

if __name__ == '__main__':
    main()
