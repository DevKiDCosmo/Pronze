import os
import sys
import shutil
import hashlib
import subprocess
import time
import urllib.request
import threading
import queue
import json
import http.server
import tarfile

# Global build state for elapsed timer and compiler analyzer
build_start_time = None
compilation_analyzer_data = {}

# -----------------------------------------------------------------------------
# 1. Colors & Logging Globals
# -----------------------------------------------------------------------------
class Logger:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    @staticmethod
    def log_plain(msg):
        print(msg)

    @staticmethod
    def log_step(msg):
        print(f"{Logger.CYAN}[•]{Logger.RESET} {msg}")

    @staticmethod
    def log_info(msg):
        print(f"{Logger.BLUE}[i]{Logger.RESET} {msg}")

    @staticmethod
    def log_success(msg):
        print(f"{Logger.GREEN}[✔]{Logger.RESET} {msg}")

    @staticmethod
    def log_warn(msg):
        print(f"{Logger.YELLOW}[!]{Logger.RESET} {msg}")

    @staticmethod
    def log_error(msg):
        print(f"{Logger.RED}[x]{Logger.RESET} {msg}", file=sys.stderr)

    @staticmethod
    def log_section(title, width=58):
        line = "=" * width
        print(f"{Logger.MAGENTA}{line}{Logger.RESET}")
        if title:
            print(f"{Logger.BOLD}{title}{Logger.RESET}")
            print(f"{Logger.MAGENTA}{line}{Logger.RESET}")

# Global flags and states
no_logs_flag = False
no_logs_terminal_flag = False
no_view_flag = False
log_buffers = {}
log_lock = threading.Lock()
current_stage_name = None
clients = []
node_states = {}
detected_changes = []
current_hashes_prefix = {}

def set_runtime_flags(no_logs, no_logs_terminal, no_view=False):
    global no_logs_flag, no_logs_terminal_flag, no_view_flag
    no_logs_flag = no_logs
    no_logs_terminal_flag = no_logs_terminal
    no_view_flag = no_view

def append_log(stage_name, text, is_error=False):
    if not stage_name:
        return
    # If no_logs_flag is True, we only capture error lines in the webview logs
    if no_logs_flag and not is_error:
        return
    with log_lock:
        if stage_name not in log_buffers:
            log_buffers[stage_name] = ""
        log_buffers[stage_name] += text

# -----------------------------------------------------------------------------
# 2. Hashing Utilities
# -----------------------------------------------------------------------------
def get_file_hash(filepath):
    if not os.path.isfile(filepath):
        return ""
    h = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""

def get_dir_hash(directory):
    if not os.path.isdir(directory):
        return ""
    
    file_hashes = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('target', '.tmp_versions', '__pycache__')]
        
        for file in files:
            if file.startswith('.'):
                continue
            if file.endswith(('.o', '.ko', '.mod', '.mod.c', '.o.d', '.so', '.img', 'modules.order', 'Module.symvers', '.pyc')):
                continue
            if file in ('test_alloc', 'test_bounds', 'test_zig', 'test_rust'):
                continue
            
            filepath = os.path.join(root, file)
            if '/target/' in filepath or '/.tmp_versions/' in filepath or '/__pycache__/' in filepath:
                continue
            
            fhash = get_file_hash(filepath)
            if fhash:
                file_hashes.append((os.path.relpath(filepath, directory), fhash))
            
    if not file_hashes:
        return "empty"
        
    file_hashes.sort(key=lambda x: x[0])
    h = hashlib.sha256()
    for relpath, fhash in file_hashes:
        h.update(f"{relpath}:{fhash}\n".encode('utf-8'))
    return h.hexdigest()

def count_compilable_files(directory, extensions=None):
    if not os.path.isdir(directory):
        return 0
    if extensions is None:
        extensions = ('.c', '.h', '.cpp', '.hpp', '.cc', '.h', '.rs', '.zig', '.S', '.s')
    count = 0
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('target', '.tmp_versions', '__pycache__', 'node_modules')]
        for file in files:
            if file.endswith(extensions):
                count += 1
    return count

def count_tarball_files(tar_path, extensions=None):
    if not os.path.exists(tar_path):
        return 0
    if extensions is None:
        extensions = ('.c', '.h', '.cpp', '.hpp', '.cc', '.h', '.rs', '.zig', '.S', '.s')
    count = 0
    try:
        with tarfile.open(tar_path) as tar:
            for member in tar.getmembers():
                if member.isfile() and member.name.endswith(extensions):
                    count += 1
    except Exception:
        pass
    return count

def analyze_compilation_files(context):
    global compilation_analyzer_data
    
    linux_ver = context.config['LINUX_VERSION']
    busybox_ver = context.config['BUSYBOX_VERSION']
    skalibs_ver = context.config.get('SKALIBS_VERSION', '0.0.0.0')
    execline_ver = context.config.get('EXECLINE_VERSION', '0.0.0.0')
    s6_ver = context.config.get('S6_VERSION', '0.0.0.0')
    
    def check_stage_cache(stage_name, expected_hash):
        if not expected_hash or expected_hash == "None":
            return False
        hash_file = os.path.join(context.nochanges_dir, f"{stage_name}.hash")
        done_file = os.path.join(context.nochanges_dir, f"{stage_name}.done")
        if os.path.exists(hash_file) and os.path.exists(done_file):
            with open(hash_file, 'r', encoding='utf-8') as f:
                saved_val = f.read().strip()
            if saved_val == expected_hash:
                return True
        return False

    # 1. Kernel
    linux_tar_hash = get_file_hash(os.path.join(context.download_dir, f"linux-{linux_ver}.tar.xz"))
    expected_kernel_hash = hashlib.sha256(f"{linux_ver}-{linux_tar_hash}".encode('utf-8')).hexdigest() if linux_tar_hash else "None"
    kernel_match = check_stage_cache("CompileKernel", expected_kernel_hash) and os.path.exists(os.path.join(context.nochanges_dir, "bzImage"))

    # 2. Busybox
    busybox_tar_hash = get_file_hash(os.path.join(context.download_dir, f"busybox-{busybox_ver}.tar.bz2"))
    expected_busybox_hash = hashlib.sha256(f"{busybox_ver}-{busybox_tar_hash}".encode('utf-8')).hexdigest() if busybox_tar_hash else "None"
    busybox_match = check_stage_cache("CompileBusybox", expected_busybox_hash) and os.path.exists(os.path.join(context.nochanges_dir, "busybox_install.tar.gz"))

    # 3. s6
    skalibs_tar_hash = get_file_hash(os.path.join(context.download_dir, f"skalibs-{skalibs_ver}.tar.gz"))
    execline_tar_hash = get_file_hash(os.path.join(context.download_dir, f"execline-{execline_ver}.tar.gz"))
    s6_tar_hash = get_file_hash(os.path.join(context.download_dir, f"s6-{s6_ver}.tar.gz"))
    expected_s6_hash = hashlib.sha256(f"{skalibs_ver}-{execline_ver}-{s6_ver}-{skalibs_tar_hash}-{execline_tar_hash}-{s6_tar_hash}".encode('utf-8')).hexdigest() if (skalibs_tar_hash and execline_tar_hash and s6_tar_hash) else "None"
    s6_match = check_stage_cache("CompileS6", expected_s6_hash) and os.path.exists(os.path.join(context.nochanges_dir, "s6_install.tar.gz"))

    # 4. Kernel Module
    kernel_mod_hash = get_dir_hash(os.path.join(context.workspace_dir, "kernel"))
    kernel_mod_match = check_stage_cache("CompileKernelModule", kernel_mod_hash) and os.path.exists(os.path.join(context.nochanges_dir, "pronze.ko"))

    # 5. SDK
    sdk_dir_hash = get_dir_hash(os.path.join(context.workspace_dir, "sdk"))
    test_dir_hash = get_dir_hash(os.path.join(context.workspace_dir, "test"))
    conf_hash = get_file_hash(os.path.join(context.workspace_dir, "pipeline.conf"))
    sdk_combined_hash = hashlib.sha256(f"{sdk_dir_hash}-{test_dir_hash}-{conf_hash}".encode('utf-8')).hexdigest()
    sdk_match = check_stage_cache("CompileSDK", sdk_combined_hash) and os.path.exists(os.path.join(context.nochanges_dir, "libpronze.so"))

    # 6. Daemon
    daemon_hash = get_dir_hash(os.path.join(context.workspace_dir, "daemon"))
    daemon_match = check_stage_cache("CompileDaemon", daemon_hash) and os.path.exists(os.path.join(context.nochanges_dir, "pronzed"))

    def get_count(match, directory, tarball_paths=None, default_val=0):
        if match:
            return 0
        if os.path.isdir(directory):
            return count_compilable_files(directory)
        if tarball_paths:
            total_t = 0
            for tp in tarball_paths:
                if os.path.exists(tp):
                    total_t += count_tarball_files(tp)
            if total_t > 0:
                return total_t
        return default_val

    kernel_cnt = get_count(kernel_match, os.path.join(context.src_dir, f"linux-{linux_ver}"), [os.path.join(context.download_dir, f"linux-{linux_ver}.tar.xz")], 60000)
    busybox_cnt = get_count(busybox_match, os.path.join(context.src_dir, f"busybox-{busybox_ver}"), [os.path.join(context.download_dir, f"busybox-{busybox_ver}.tar.bz2")], 1000)
    
    s6_tarball_paths = [
        os.path.join(context.download_dir, f"skalibs-{skalibs_ver}.tar.gz"),
        os.path.join(context.download_dir, f"execline-{execline_ver}.tar.gz"),
        os.path.join(context.download_dir, f"s6-{s6_ver}.tar.gz")
    ]
    s6_cnt = 0
    if not s6_match:
        for subdir in [f"skalibs-{skalibs_ver}", f"execline-{execline_ver}", f"s6-{s6_ver}"]:
            sd = os.path.join(context.src_dir, subdir)
            if os.path.isdir(sd):
                s6_cnt += count_compilable_files(sd)
        if s6_cnt == 0:
            for tp in s6_tarball_paths:
                s6_cnt += count_tarball_files(tp)
        if s6_cnt == 0:
            s6_cnt = 800

    mod_cnt = get_count(kernel_mod_match, os.path.join(context.workspace_dir, "kernel"), None, 5)
    
    sdk_cnt = 0
    if not sdk_match:
        sdk_cnt += count_compilable_files(os.path.join(context.workspace_dir, "sdk"))
        sdk_cnt += count_compilable_files(os.path.join(context.workspace_dir, "test"))
        
    daemon_cnt = get_count(daemon_match, os.path.join(context.workspace_dir, "daemon"), None, 10)

    compilation_analyzer_data = {
        "CompileKernel": {"files": kernel_cnt, "status": "Cached" if kernel_match else "Needs Compile"},
        "CompileBusybox": {"files": busybox_cnt, "status": "Cached" if busybox_match else "Needs Compile"},
        "CompileS6": {"files": s6_cnt, "status": "Cached" if s6_match else "Needs Compile"},
        "CompileKernelModule": {"files": mod_cnt, "status": "Cached" if kernel_mod_match else "Needs Compile"},
        "CompileSDK": {"files": sdk_cnt, "status": "Cached" if sdk_match else "Needs Compile"},
        "CompileDaemon": {"files": daemon_cnt, "status": "Cached" if daemon_match else "Needs Compile"},
        "total_files": kernel_cnt + busybox_cnt + s6_cnt + mod_cnt + sdk_cnt + daemon_cnt
    }

def compute_all_hashes(context):
    linux_ver = context.config['LINUX_VERSION']
    busybox_ver = context.config['BUSYBOX_VERSION']
    skalibs_ver = context.config.get('SKALIBS_VERSION', '0.0.0.0')
    execline_ver = context.config.get('EXECLINE_VERSION', '0.0.0.0')
    s6_ver = context.config.get('S6_VERSION', '0.0.0.0')

    linux_tar = os.path.join(context.download_dir, f"linux-{linux_ver}.tar.xz")
    busybox_tar = os.path.join(context.download_dir, f"busybox-{busybox_ver}.tar.bz2")
    skalibs_tar = os.path.join(context.download_dir, f"skalibs-{skalibs_ver}.tar.gz")
    execline_tar = os.path.join(context.download_dir, f"execline-{execline_ver}.tar.gz")
    s6_tar = os.path.join(context.download_dir, f"s6-{s6_ver}.tar.gz")

    hashes = {
        "kernel_dir": get_dir_hash(os.path.join(context.workspace_dir, "kernel")),
        "sdk_dir": get_dir_hash(os.path.join(context.workspace_dir, "sdk")),
        "daemon_dir": get_dir_hash(os.path.join(context.workspace_dir, "daemon")),
        "s6_dir": get_dir_hash(os.path.join(context.workspace_dir, "s6")),
        "test_dir": get_dir_hash(os.path.join(context.workspace_dir, "test")),
        "profiles_dir": get_dir_hash(os.path.join(context.workspace_dir, "profiles")),
        "build_pipeline_dir": get_dir_hash(os.path.join(context.workspace_dir, "build-pipeline")),
        "pipeline_conf": get_file_hash(os.path.join(context.workspace_dir, "pipeline.conf")),
        "linux_ver": linux_ver,
        "busybox_ver": busybox_ver,
        "skalibs_ver": skalibs_ver,
        "execline_ver": execline_ver,
        "s6_ver": s6_ver,
        "linux_tar": get_file_hash(linux_tar),
        "busybox_tar": get_file_hash(busybox_tar),
        "skalibs_tar": get_file_hash(skalibs_tar),
        "execline_tar": get_file_hash(execline_tar),
        "s6_tar": get_file_hash(s6_tar),
    }
    return hashes

def generate_diff_report(context):
    global detected_changes, current_hashes_prefix
    detected_changes = []
    
    current_hashes = compute_all_hashes(context)
    
    for k, v in current_hashes.items():
        if len(v) > 12:
            current_hashes_prefix[k] = v[:8]
        else:
            current_hashes_prefix[k] = v
            
    hashes_file = os.path.join(context.nochanges_dir, "hashes.json")
    old_hashes = {}
    if os.path.exists(hashes_file):
        try:
            with open(hashes_file, 'r') as f:
                old_hashes = json.load(f)
        except Exception:
            pass

    components = [
        ("kernel_dir", "Kernel Directory"),
        ("sdk_dir", "SDK Directory"),
        ("daemon_dir", "Daemon Directory"),
        ("s6_dir", "s6 Directory"),
        ("test_dir", "Test Directory"),
        ("profiles_dir", "Profiles Directory"),
        ("build_pipeline_dir", "Pipeline Directory"),
        ("pipeline_conf", "pipeline.conf"),
        ("linux_ver", "Linux Version"),
        ("busybox_ver", "Busybox Version"),
        ("skalibs_ver", "skalibs Version"),
        ("execline_ver", "execline Version"),
        ("s6_ver", "s6 Version"),
        ("linux_tar", "Linux Tarball"),
        ("busybox_tar", "Busybox Tarball"),
        ("skalibs_tar", "skalibs Tarball"),
        ("execline_tar", "execline Tarball"),
        ("s6_tar", "s6 Tarball"),
    ]
    
    for key, name in components:
        old_val = old_hashes.get(key, "")
        new_val = current_hashes.get(key, "")
        
        old_short = old_val[:8] if len(old_val) > 12 else old_val
        new_short = new_val[:8] if len(new_val) > 12 else new_val
        
        # If old_val was empty, we display "None" instead of "N/A"
        if not old_short:
            old_short = "None"
        if not new_short:
            new_short = "None"
        
        if old_val != new_val:
            detected_changes.append({
                "component": name,
                "old": old_short,
                "new": new_short
            })

# -----------------------------------------------------------------------------
# 3. GUI Dashboard HTML
# -----------------------------------------------------------------------------
HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>PronzeOS Developer Console</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #faf9f6;
            --border-color: #1a1a1a;
            --text-color: #1a1a1a;
            --text-muted: #666666;
            --accent-color: #000000;
        }

        body {
            background-color: var(--bg-color);
            background-image: radial-gradient(#d3d2cb 1.5px, transparent 1.5px);
            background-size: 20px 20px;
            color: var(--text-color);
            font-family: 'Outfit', sans-serif;
            margin: 0;
            padding: 1.5rem;
        }

        .dashboard-container {
            max-width: 1400px;
            margin: 0 auto;
            display: grid;
            grid-template-columns: 340px 1fr;
            gap: 1.5rem;
        }

        /* Akkon-style Box */
        .console-box {
            border: 3px solid var(--border-color);
            background: #ffffff;
            box-shadow: 4px 4px 0px var(--border-color);
            margin-bottom: 1.5rem;
            position: relative;
            padding: 1.25rem;
        }

        .console-box-title {
            font-family: 'JetBrains Mono', monospace;
            font-weight: 800;
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 3px solid var(--border-color);
            margin: -1.25rem -1.25rem 1rem -1.25rem;
            padding: 0.5rem 1.25rem;
            background: #1a1a1a;
            color: #ffffff;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        /* Header bar */
        .top-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 3px solid var(--border-color);
            padding-bottom: 1rem;
            margin-bottom: 1.5rem;
        }

        .top-header h1 {
            margin: 0;
            font-weight: 800;
            font-size: 1.8rem;
            letter-spacing: -0.03em;
            text-transform: uppercase;
        }

        .status-badge {
            border: 3px solid var(--border-color);
            padding: 0.25rem 0.75rem;
            font-family: 'JetBrains Mono', monospace;
            font-weight: 800;
            text-transform: uppercase;
            font-size: 0.85rem;
            background: #ffffff;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            box-shadow: 2px 2px 0px var(--border-color);
        }

        .status-dot {
            width: 10px;
            height: 10px;
            background: #adb5bd;
            display: inline-block;
        }

        .status-dot.building { background: #fcc419; animation: blink 1s infinite; }
        .status-dot.success { background: #37b24d; }
        .status-dot.failed { background: #f03e3e; }

        @keyframes blink {
            50% { opacity: 0; }
        }

        /* Metrics list */
        .metric-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 0.5rem;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
            font-weight: bold;
        }

        /* Progress Bar */
        .progress-container {
            border: 3px solid var(--border-color);
            height: 20px;
            background: #ffffff;
            position: relative;
            margin-bottom: 1rem;
        }

        .progress-bar-fill {
            height: 100%;
            background: var(--border-color);
            width: 0%;
            transition: width 0.3s ease;
        }

        /* Changes box */
        .changes-list {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
            max-height: 200px;
            overflow-y: auto;
            border: 2px solid #ccc;
            padding: 0.5rem;
            background: #fdfdfd;
        }

        .change-item {
            margin-bottom: 0.25rem;
            border-bottom: 1px dashed #eee;
            padding-bottom: 0.25rem;
        }

        /* DAG layout */
        .dag-tree {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .stage-row {
            display: flex;
            justify-content: center;
            gap: 1.5rem;
            position: relative;
        }

        .stage-row::after {
            content: '';
            position: absolute;
            bottom: -1.5rem;
            left: 50%;
            width: 3px;
            height: 1.5rem;
            background: var(--border-color);
            z-index: 0;
        }

        .stage-row:last-child::after {
            display: none;
        }

        /* Node Card */
        .node-card {
            border: 3px solid var(--border-color);
            background: #ffffff;
            padding: 0.75rem 1rem;
            width: 200px;
            box-shadow: 4px 4px 0px var(--border-color);
            position: relative;
            cursor: pointer;
            z-index: 2;
            transition: transform 0.1s, box-shadow 0.1s;
        }

        .node-card:active {
            transform: translate(2px, 2px);
            box-shadow: 2px 2px 0px var(--border-color);
        }

        .node-card.selected {
            background: #fff9db !important;
            border-color: #f59f00;
            box-shadow: 4px 4px 0px #f59f00;
        }

        .node-card::before {
            content: '';
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 6px;
            background: #ced4da;
        }

        .node-card.pending::before { background: #868e96; }
        .node-card.running::before { background: #fcc419; }
        .node-card.success::before { background: #37b24d; }
        .node-card.failed::before { background: #f03e3e; }
        .node-card.skipped::before { background: #adb5bd; border-left: 2px dashed #495057; }

        .node-card.skipped {
            border-style: dashed;
            opacity: 0.75;
        }

        .node-name {
            font-family: 'JetBrains Mono', monospace;
            font-weight: 800;
            font-size: 0.85rem;
            margin-bottom: 0.25rem;
            text-transform: uppercase;
        }

        .node-status-label {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.7rem;
            font-weight: bold;
            color: var(--text-muted);
            text-transform: uppercase;
        }

        .node-duration {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.7rem;
            font-weight: bold;
            color: #495057;
            float: right;
        }

        .duration-table {
            width: 100%;
            border-collapse: collapse;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
            margin-top: 1rem;
        }

        .duration-table th, .duration-table td {
            border: 2px solid var(--border-color);
            padding: 0.4rem;
            text-align: left;
        }

        .duration-table th {
            background: #f1f3f5;
        }
    </style>
</head>
<body>
    <div class="top-header">
        <h1>PRONZE OS BUILD SYSTEM</h1>
        <div class="status-badge" id="global-status">
            <span class="status-dot" id="global-status-dot"></span>
            <span id="global-status-text">IDLE</span>
        </div>
    </div>

    <div class="dashboard-container">
        <!-- Left Side: System Health and Config -->
        <div class="left-panel">
            <div class="console-box">
                <div class="console-box-title">SYSTEM MONITOR</div>
                <div class="metric-row">
                    <span>RELIABILITY SCORE</span>
                    <span>100%</span>
                </div>
                <div class="progress-container">
                    <div class="progress-bar-fill" style="width: 100%;"></div>
                </div>

                <div class="metric-row">
                    <span>BUILD PROGRESS</span>
                    <span id="build-progress-percent">0%</span>
                </div>
                <div class="progress-container">
                    <div class="progress-bar-fill" id="build-progress-bar" style="width: 0%;"></div>
                </div>

                <div class="metric-row">
                    <span>ELAPSED TIME</span>
                    <span id="elapsed-time">00:00</span>
                </div>
                <div class="metric-row">
                    <span>CACHE INTEGRITY</span>
                    <span id="cache-integrity">UNKNOWN</span>
                </div>
            </div>

            <div class="console-box">
                <div class="console-box-title">COMPILATION ANALYZER</div>
                <div class="metric-row">
                    <span>FILES TO COMPILE</span>
                    <span id="analyzer-total-files">0</span>
                </div>
                <div class="changes-list" id="analyzer-details-container">
                    <div style="color: #666; font-style: italic;">Awaiting compilation details...</div>
                </div>
            </div>

            <div class="console-box">
                <div class="console-box-title">DETECTED CHANGES</div>
                <div class="changes-list" id="changes-container">
                    <div style="color: #666; font-style: italic;">Awaiting compilation changes report...</div>
                </div>
            </div>
        </div>

        <!-- Right Side: DAG -->
        <div class="right-panel">
            <div class="console-box">
                <div class="console-box-title">PIPELINE STAGES (DAG)</div>
                <div class="dag-tree">
                    <!-- Level 1 -->
                    <div class="stage-row">
                        <div class="node-card pending" id="node-DownloadTarballs" onclick="selectStage('DownloadTarballs')">
                            <div class="node-name">Download Tarballs</div>
                            <span class="node-status-label" id="status-DownloadTarballs">Pending</span>
                            <span class="node-duration" id="duration-DownloadTarballs"></span>
                        </div>
                    </div>

                    <!-- Level 2 -->
                    <div class="stage-row">
                        <div class="node-card pending" id="node-CheckEarlyExit" onclick="selectStage('CheckEarlyExit')">
                            <div class="node-name">Verify Cache</div>
                            <span class="node-status-label" id="status-CheckEarlyExit">Pending</span>
                            <span class="node-duration" id="duration-CheckEarlyExit"></span>
                        </div>
                    </div>

                    <!-- Level 3 -->
                    <div class="stage-row">
                        <div class="node-card pending" id="node-ExtractTarballs" onclick="selectStage('ExtractTarballs')">
                            <div class="node-name">Extract Sources</div>
                            <span class="node-status-label" id="status-ExtractTarballs">Pending</span>
                            <span class="node-duration" id="duration-ExtractTarballs"></span>
                        </div>
                    </div>

                    <!-- Level 4 -->
                    <div class="stage-row">
                        <div class="node-card pending" id="node-CompileKernel" onclick="selectStage('CompileKernel')">
                            <div class="node-name">Compile Kernel</div>
                            <span class="node-status-label" id="status-CompileKernel">Pending</span>
                            <span class="node-duration" id="duration-CompileKernel"></span>
                        </div>
                        <div class="node-card pending" id="node-CompileBusybox" onclick="selectStage('CompileBusybox')">
                            <div class="node-name">Compile BusyBox</div>
                            <span class="node-status-label" id="status-CompileBusybox">Pending</span>
                            <span class="node-duration" id="duration-CompileBusybox"></span>
                        </div>
                        <div class="node-card pending" id="node-CompileS6" onclick="selectStage('CompileS6')">
                            <div class="node-name">Compile s6</div>
                            <span class="node-status-label" id="status-CompileS6">Pending</span>
                            <span class="node-duration" id="duration-CompileS6"></span>
                        </div>
                    </div>
                    <div class="stage-row">
                        <div class="node-card pending" id="node-CompileKernelModule" onclick="selectStage('CompileKernelModule')">
                            <div class="node-name">Compile Driver</div>
                            <span class="node-status-label" id="status-CompileKernelModule">Pending</span>
                            <span class="node-duration" id="duration-CompileKernelModule"></span>
                        </div>
                        <div class="node-card pending" id="node-CompileSDK" onclick="selectStage('CompileSDK')">
                            <div class="node-name">Compile SDK</div>
                            <span class="node-status-label" id="status-CompileSDK">Pending</span>
                            <span class="node-duration" id="duration-CompileSDK"></span>
                        </div>
                        <div class="node-card pending" id="node-CompileDaemon" onclick="selectStage('CompileDaemon')">
                            <div class="node-name">Compile Daemon</div>
                            <span class="node-status-label" id="status-CompileDaemon">Pending</span>
                            <span class="node-duration" id="duration-CompileDaemon"></span>
                        </div>
                    </div>

                    <!-- Level 5 -->
                    <div class="stage-row">
                        <div class="node-card pending" id="node-AssembleRootfs" onclick="selectStage('AssembleRootfs')">
                            <div class="node-name">Assemble RootFS</div>
                            <span class="node-status-label" id="status-AssembleRootfs">Pending</span>
                            <span class="node-duration" id="duration-AssembleRootfs"></span>
                        </div>
                    </div>

                    <!-- Level 6 -->
                    <div class="stage-row">
                        <div class="node-card pending" id="node-PackageBtrfsImage" onclick="selectStage('PackageBtrfsImage')">
                            <div class="node-name">Btrfs Rootfs</div>
                            <span class="node-status-label" id="status-PackageBtrfsImage">Pending</span>
                            <span class="node-duration" id="duration-PackageBtrfsImage"></span>
                        </div>
                        <div class="node-card pending" id="node-PackageESPImage" onclick="selectStage('PackageESPImage')">
                            <div class="node-name">ESP Boot</div>
                            <span class="node-status-label" id="status-PackageESPImage">Pending</span>
                            <span class="node-duration" id="duration-PackageESPImage"></span>
                        </div>
                    </div>

                    <!-- Level 7 -->
                    <div class="stage-row">
                        <div class="node-card pending" id="node-AssembleGPTImage" onclick="selectStage('AssembleGPTImage')">
                            <div class="node-name">Assemble GPT UEFI</div>
                            <span class="node-status-label" id="status-AssembleGPTImage">Pending</span>
                            <span class="node-duration" id="duration-AssembleGPTImage"></span>
                        </div>
                    </div>

                    <!-- Level 8 -->
                    <div class="stage-row">
                        <div class="node-card pending" id="node-ShipImage" onclick="selectStage('ShipImage')">
                            <div class="node-name">Ship Image</div>
                            <span class="node-status-label" id="status-ShipImage">Pending</span>
                            <span class="node-duration" id="duration-ShipImage"></span>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="console-box" id="final-report-box" style="display: none;">
                <div class="console-box-title">FINAL BUILD TIMING REPORT</div>
                <div id="final-report-content"></div>
            </div>
        </div>
    </div>

    <script>
        let selectedStageName = "DownloadTarballs";
        let serverStartTime = null;
        let clientServerOffset = 0;
        let timerInterval = null;
        let buildRunning = false;
        
        function selectStage(name) {
            selectedStageName = name;
            document.querySelectorAll(".node-card").forEach(el => el.classList.remove("selected"));
            const el = document.getElementById("node-" + name);
            if (el) el.classList.add("selected");
        }

        // Start elapsed timer using server start time and current time
        function startTimer(startTimeVal, serverCurrTimeVal) {
            if (!startTimeVal) return;
            serverStartTime = startTimeVal;
            clientServerOffset = Date.now() - (serverCurrTimeVal * 1000);
            buildRunning = true;
            
            if (timerInterval) clearInterval(timerInterval);
            timerInterval = setInterval(updateTimerDisplay, 1000);
            updateTimerDisplay();
        }

        function updateTimerDisplay() {
            if (!serverStartTime) return;
            let elapsed = Math.floor((Date.now() - clientServerOffset - (serverStartTime * 1000)) / 1000);
            if (elapsed < 0) elapsed = 0;
            let mins = Math.floor(elapsed / 60).toString().padStart(2, '0');
            let secs = (elapsed % 60).toString().padStart(2, '0');
            document.getElementById("elapsed-time").textContent = `${mins}:${secs}`;
        }

        function stopTimer() {
            buildRunning = false;
            if (timerInterval) {
                clearInterval(timerInterval);
                timerInterval = null;
            }
        }

        const evtSource = new EventSource("/events");
        
        evtSource.addEventListener("state", (e) => {
            const payload = JSON.parse(e.data);
            const states = payload.nodes || payload;
            let activeCount = 0;
            let totalCount = Object.keys(states).length;
            let successCount = 0;
            
            for (const [name, data] of Object.entries(states)) {
                updateNode(name, data.status, data.details, data.elapsed);
                if (data.status === "Success" || data.status === "Skipped") {
                    successCount++;
                }
                if (data.status === "Running") {
                    activeCount++;
                    selectStage(name);
                }
            }
            
            if (payload.build_start_time) {
                startTimer(payload.build_start_time, payload.server_current_time);
                updateGlobalStatus("BUILDING", "building");
            } else if (activeCount > 0 && !buildRunning) {
                // Fallback
                serverStartTime = Date.now() / 1000;
                clientServerOffset = 0;
                buildRunning = true;
                timerInterval = setInterval(updateTimerDisplay, 1000);
                updateGlobalStatus("BUILDING", "building");
            }
            
            updateBuildProgress(successCount, totalCount);
        });
        
        evtSource.addEventListener("update", (e) => {
            const data = JSON.parse(e.data);
            updateNode(data.name, data.status, data.details, data.elapsed);
            
            if (data.status === "Running") {
                if (data.build_start_time) {
                    startTimer(data.build_start_time, data.server_current_time);
                } else if (!buildRunning) {
                    serverStartTime = Date.now() / 1000;
                    clientServerOffset = 0;
                    buildRunning = true;
                    timerInterval = setInterval(updateTimerDisplay, 1000);
                }
                updateGlobalStatus("BUILDING", "building");
                selectStage(data.name);
            }
            
            // Recalculate progress
            let cards = document.querySelectorAll(".node-card");
            let successCount = 0;
            cards.forEach(card => {
                if (card.classList.contains("success") || card.classList.contains("skipped")) {
                    successCount++;
                }
            });
            updateBuildProgress(successCount, cards.length);
        });

        evtSource.addEventListener("analyzer_report", (e) => {
            const data = JSON.parse(e.data);
            document.getElementById("analyzer-total-files").textContent = data.total_files;
            
            const container = document.getElementById("analyzer-details-container");
            let html = "";
            for (const [stage, info] of Object.entries(data)) {
                if (stage === "total_files") continue;
                
                let friendlyName = stage.replace("Compile", "");
                if (friendlyName === "KernelModule") friendlyName = "Kernel Module";
                
                let statusColor = info.status === "Cached" ? "#2b8a3e" : "#e03131";
                html += `<div class="change-item">` +
                        `<strong>${friendlyName}</strong>: ` +
                        `<span style="color: ${statusColor}; font-weight: bold;">${info.status}</span> ` +
                        `(${info.files} files)` +
                        `</div>`;
            }
            container.innerHTML = html;
        });

        evtSource.addEventListener("hashes_report", (e) => {
            const data = JSON.parse(e.data);
            const container = document.getElementById("changes-container");
            const integrityEl = document.getElementById("cache-integrity");
            
            if (data.changes && data.changes.length > 0) {
                integrityEl.textContent = "MISMATCH";
                integrityEl.style.color = "#f03e3e";
                
                let html = "";
                data.changes.forEach(c => {
                    html += `<div class="change-item">` +
                            `<strong>${c.component}</strong><br>` +
                            `<span style="color: #e03131;">Old: ${c.old}</span> → ` +
                            `<span style="color: #2b8a3e;">New: ${c.new}</span>` +
                            `</div>`;
                });
                container.innerHTML = html;
            } else {
                integrityEl.textContent = "MATCH";
                integrityEl.style.color = "#37b24d";
                container.innerHTML = `<div style="color: #2b8a3e; font-weight: bold;">[✔] Cache matches workspace source.</div>`;
            }
        });

        evtSource.addEventListener("total_report", (e) => {
            stopTimer();
            const data = JSON.parse(e.data);
            updateGlobalStatus(data.status.toUpperCase(), data.status.toLowerCase() === "complete" ? "success" : "failed");
            
            const box = document.getElementById("final-report-box");
            box.style.display = "block";
            
            let tableHtml = `<table class="duration-table">` +
                            `<thead><tr><th>Stage</th><th>Status</th><th>Duration</th></tr></thead>` +
                            `<tbody>`;
            
            data.report_table.forEach(row => {
                tableHtml += `<tr>` +
                             `<td><strong>${row.name}</strong></td>` +
                             `<td>${row.status}</td>` +
                             `<td>${row.elapsed}</td>` +
                             `</tr>`;
            });
            
            tableHtml += `</tbody></table>`;
            tableHtml += `<h3 style="margin-top: 1rem; font-family: monospace;">Total Duration: ${data.total_time}</h3>`;
            
            document.getElementById("final-report-content").innerHTML = tableHtml;
        });

        function updateNode(name, status, details, elapsed) {
            const el = document.getElementById("node-" + name);
            if (!el) return;
            
            el.className = "node-card " + status.toLowerCase();
            if (name === selectedStageName) {
                el.classList.add("selected");
            }
            
            const statusEl = document.getElementById("status-" + name);
            if (statusEl) statusEl.textContent = status;
            
            const durEl = document.getElementById("duration-" + name);
            if (durEl) {
                if (elapsed !== null && elapsed !== undefined) {
                    durEl.textContent = elapsed.toFixed(2) + "s";
                } else if (status === "Skipped") {
                    durEl.textContent = "skipped";
                } else {
                    durEl.textContent = "";
                }
            }
        }

        function updateGlobalStatus(text, className) {
            const textEl = document.getElementById("global-status-text");
            const dotEl = document.getElementById("global-status-dot");
            
            textEl.textContent = text;
            dotEl.className = "status-dot " + className;
        }

        function updateBuildProgress(success, total) {
            if (total === 0) return;
            const percent = Math.floor((success / total) * 100);
            document.getElementById("build-progress-percent").textContent = percent + "%";
            document.getElementById("build-progress-bar").style.width = percent + "%";
        }

        // Set default selection
        selectStage("DownloadTarballs");
    </script>
</body>
</html>
"""

def update_node_status(name, status, details="", elapsed=None):
    with log_lock:
        logs = log_buffers.get(name, "")
    
    node_states[name] = {"status": status, "details": details, "elapsed": elapsed}
    
    # If no_view is enabled, do not attempt to stream events
    if no_view_flag:
        return
        
    payload = {
        "name": name,
        "status": status,
        "details": details,
        "elapsed": elapsed,
        "logs": logs,
        "build_start_time": build_start_time,
        "server_current_time": time.time()
    }
    msg = f"event: update\ndata: {json.dumps(payload)}\n\n"
    for q in list(clients):
        q.put(msg)

def send_total_report(total_time, status_text):
    rows = []
    for node_name in node_states:
        state = node_states.get(node_name, {})
        elapsed = state.get("elapsed")
        elapsed_str = f"{elapsed:.2f}s" if elapsed is not None else ("skipped" if state.get("status") == "Skipped" else "0.00s")
        rows.append({"name": node_name, "status": state.get("status"), "elapsed": elapsed_str})
        
    # If no_view is enabled, do not attempt to stream events
    if no_view_flag:
        return

    payload = {
        "total_time": f"{total_time:.2f}s",
        "status": status_text,
        "report_table": rows
    }
    msg = f"event: total_report\ndata: {json.dumps(payload)}\n\n"
    for q in list(clients):
        q.put(msg)

class SSEHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
        elif self.path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            q = queue.Queue()
            clients.append(q)
            
            state_payload = {
                "nodes": node_states,
                "build_start_time": build_start_time,
                "server_current_time": time.time()
            }
            initial_state_msg = f"event: state\ndata: {json.dumps(state_payload)}\n\n"
            with log_lock:
                initial_logs_msg = f"event: initial_logs\ndata: {json.dumps(log_buffers)}\n\n"
            hashes_payload = {
                "changes": detected_changes,
                "current_hashes": current_hashes_prefix
            }
            initial_hashes_msg = f"event: hashes_report\ndata: {json.dumps(hashes_payload)}\n\n"
            analyzer_msg = f"event: analyzer_report\ndata: {json.dumps(compilation_analyzer_data)}\n\n"
            
            try:
                self.wfile.write(initial_state_msg.encode('utf-8'))
                self.wfile.write(initial_logs_msg.encode('utf-8'))
                self.wfile.write(initial_hashes_msg.encode('utf-8'))
                self.wfile.write(analyzer_msg.encode('utf-8'))
                self.wfile.flush()
            except Exception:
                if q in clients:
                    clients.remove(q)
                return

            while True:
                try:
                    msg = q.get(timeout=1.0)
                    self.wfile.write(msg.encode('utf-8'))
                    self.wfile.flush()
                except queue.Empty:
                    try:
                        self.wfile.write(":\n\n".encode('utf-8'))
                        self.wfile.flush()
                    except Exception:
                        break
                except Exception:
                    break
            if q in clients:
                clients.remove(q)
        else:
            self.send_error(404)

def start_http_server(port=8000):
    if no_view_flag:
        return
    server = http.server.ThreadingHTTPServer(('0.0.0.0', port), SSEHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    Logger.log_info(f"HTML GUI visualization server started on http://localhost:{port}")

# -----------------------------------------------------------------------------
# 4. Pipeline Infrastructure (DAG Context, Nodes, and Graph Exec)
# -----------------------------------------------------------------------------
class PipelineContext:
    def __init__(self, target, workspace_dir, opt_dir):
        self.target = target
        self.workspace_dir = workspace_dir
        self.opt_dir = opt_dir
        self.download_dir = os.path.join(opt_dir, "downloads")
        self.src_dir = os.path.join(opt_dir, "src")
        self.output_dir = os.path.join(workspace_dir, "output")
        self.work_dir = "/tmp/pronze_build"
        self.rootfs_dir = os.path.join(self.work_dir, "rootfs")
        self.nochanges_dir = os.path.join(workspace_dir, ".output-nochanges")
        self.master_hash = ""
        self.overall_start_t = 0.0
        self.skip_remaining = False

        os.makedirs(self.opt_dir, exist_ok=True)
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(self.src_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.work_dir, exist_ok=True)
        os.makedirs(self.rootfs_dir, exist_ok=True)
        os.makedirs(self.nochanges_dir, exist_ok=True)

        # Migrate any legacy files from .builthash to .output-nochanges
        legacy_dir = os.path.join(workspace_dir, ".builthash")
        if os.path.isdir(legacy_dir):
            import shutil
            Logger.log_info(f"Migrating legacy files from {legacy_dir} to {self.nochanges_dir}...")
            for item in os.listdir(legacy_dir):
                s = os.path.join(legacy_dir, item)
                d = os.path.join(self.nochanges_dir, item)
                try:
                    if os.path.isdir(s):
                        if os.path.exists(d):
                            shutil.rmtree(d)
                        shutil.copytree(s, d)
                    else:
                        shutil.copy2(s, d)
                except Exception as e:
                    Logger.log_warn(f"Failed to copy legacy file {item}: {e}")
            try:
                shutil.rmtree(legacy_dir)
            except Exception as e:
                Logger.log_warn(f"Failed to remove legacy directory {legacy_dir}: {e}")

        # Also migrate/cleanup container-level /.builthash if it exists and we have permissions
        container_legacy_dir = "/.builthash"
        container_new_dir = "/.output-nochanges"
        if os.path.isdir(container_legacy_dir):
            try:
                import shutil
                if os.path.exists(container_new_dir):
                    for item in os.listdir(container_legacy_dir):
                        s = os.path.join(container_legacy_dir, item)
                        d = os.path.join(container_new_dir, item)
                        if os.path.isdir(s):
                            if os.path.exists(d):
                                shutil.rmtree(d)
                            shutil.copytree(s, d)
                        else:
                            shutil.copy2(s, d)
                shutil.rmtree(container_legacy_dir)
            except Exception:
                pass

        self.config = self._load_config()

    def _load_config(self):
        config_path = os.path.join(self.workspace_dir, "pipeline.conf")
        config = {}
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parts = line.split('=', 1)
                        if len(parts) == 2:
                            config[parts[0].strip()] = parts[1].strip().strip('"').strip("'")
        return config

class PipelineNode:
    def __init__(self, name, dependencies=None):
        self.name = name
        self.dependencies = dependencies or []
        node_states[name] = {"status": "Pending", "details": "Waiting in queue...", "elapsed": None}

    def run(self, context):
        raise NotImplementedError

class Pipeline:
    def __init__(self):
        self.nodes = {}
        self.execution_order = []

    def add_node(self, node):
        self.nodes[node.name] = node

    def build_graph(self):
        visited = set()
        temp_visited = set()

        def visit(node_name):
            if node_name in temp_visited:
                raise Exception(f"Cyclic dependency detected: {node_name}")
            if node_name not in visited:
                temp_visited.add(node_name)
                node = self.nodes.get(node_name)
                if not node:
                    raise Exception(f"Registered node dependency not found: {node_name}")
                for dep in node.dependencies:
                    visit(dep)
                temp_visited.remove(node_name)
                visited.add(node_name)
                self.execution_order.append(node)

        for node_name in self.nodes:
            visit(node_name)

    def execute(self, context):
        global current_stage_name, build_start_time
        overall_start_t = time.time()
        build_start_time = overall_start_t
        context.overall_start_t = overall_start_t
        context.pipeline = self
        
        for node in self.execution_order:
            if getattr(context, 'skip_remaining', False):
                update_node_status(node.name, "Skipped", "Cached (Early Exit)")
                continue
                
            Logger.log_section(f"Stage: {node.name}")
            current_stage_name = node.name
            start_t = time.time()
            update_node_status(node.name, "Running", "Compiling/Processing")
            try:
                node.run(context)
                elapsed_t = time.time() - start_t
                if node_states[node.name]["status"] == "Running":
                    update_node_status(node.name, "Success", f"Complete ({elapsed_t:.2f}s)", elapsed=elapsed_t)
                elif node_states[node.name]["status"] == "Success" and node_states[node.name].get("elapsed") is None:
                    update_node_status(node.name, "Success", node_states[node.name].get("details", ""), elapsed=elapsed_t)
            except Exception as e:
                elapsed_t = time.time() - start_t
                update_node_status(node.name, "Failed", f"Error ({elapsed_t:.2f}s)", elapsed=elapsed_t)
                Logger.log_error(f"Failed executing {node.name}: {e}")
                sys.exit(1)

        total_time = time.time() - overall_start_t
        Logger.log_section("          Build Completion Report          ")
        for node in self.execution_order:
            state = node_states.get(node.name, {})
            duration = state.get("elapsed")
            duration_str = f"{duration:.2f}s" if duration is not None else ("Skipped" if state.get("status") == "Skipped" else "0.00s")
            Logger.log_plain(f"  - {node.name:<25}: {state.get('status'):<10} ({duration_str})")
        Logger.log_plain(f"\n  [✔] Total Build Duration: {total_time:.2f}s")
        Logger.log_section("")

        send_total_report(total_time, "Complete")

def run_cmd(cmd, cwd=None, env=None, input_data=None):
    stage_name = current_stage_name
    
    append_log(stage_name, f"$ {cmd}\n")
    
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdin=subprocess.PIPE if input_data else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
        env=env
    )
    
    if input_data:
        try:
            process.stdin.write(input_data)
            process.stdin.close()
        except Exception as e:
            err_msg = f"Failed writing to stdin: {e}\n"
            append_log(stage_name, err_msg, is_error=True)
            if not no_logs_terminal_flag:
                sys.stderr.write(err_msg)
            
    captured_lines = []
    while True:
        line = process.stdout.readline()
        if not line:
            break
        captured_lines.append(line)
        append_log(stage_name, line)
        if not no_logs_terminal_flag:
            sys.stdout.write(line)
            sys.stdout.flush()
            
    process.wait()
    
    if process.returncode != 0:
        err_msg = f"Command failed with exit code {process.returncode}: {cmd}\n"
        append_log(stage_name, err_msg, is_error=True)
        if no_logs_terminal_flag:
            Logger.log_error(f"Command failed: {cmd}\nCaptured logs:\n" + "".join(captured_lines))
        else:
            Logger.log_error(err_msg)
        raise subprocess.CalledProcessError(process.returncode, cmd, output="".join(captured_lines))

def copy_file_or_symlink(src, dest):
    if os.path.isdir(dest) and not os.path.islink(dest):
        dest = os.path.join(dest, os.path.basename(src))

    if os.path.islink(src):
        linkto = os.readlink(src)
        if os.path.exists(dest) or os.path.islink(dest):
            if os.path.isdir(dest) and not os.path.islink(dest):
                shutil.rmtree(dest)
            else:
                os.remove(dest)
        os.symlink(linkto, dest)
    else:
        shutil.copy2(src, dest)

def copy_dir_contents(src, dest):
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dest, item)
        if os.path.isdir(s) and not os.path.islink(s):
            shutil.copytree(s, d, symlinks=True, dirs_exist_ok=True)
        else:
            copy_file_or_symlink(s, d)

def write_text_file(filepath, content):
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

def write_json_file(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def write_stage_flags(context, stage_name, stage_hash):
    hash_file = os.path.join(context.nochanges_dir, f"{stage_name}.hash")
    done_file = os.path.join(context.nochanges_dir, f"{stage_name}.done")
    with open(hash_file, 'w', encoding='utf-8') as f:
        f.write(stage_hash + "\n")
    with open(done_file, 'w', encoding='utf-8') as f:
        f.write("OK\n")

def print_status_table(context):
    Logger.log_section("          PronzeOS Build Status Report          ")
    
    # Check what is currently cached/available
    linux_ver = context.config['LINUX_VERSION']
    busybox_ver = context.config['BUSYBOX_VERSION']
    s6_ver = context.config['S6_VERSION']

    # Checking tarballs
    def tar_status(name, ver, ext):
        path = os.path.join(context.download_dir, f"{name}-{ver}.{ext}")
        if os.path.exists(path):
            return "OK", get_file_hash(path)[:8]
        return "Missing", "None"

    linux_stat, linux_hash = tar_status("linux", linux_ver, "tar.xz")
    busybox_stat, busybox_hash = tar_status("busybox", busybox_ver, "tar.bz2")
    s6_stat, s6_hash = tar_status("s6", s6_ver, "tar.gz")

    # Checking caches
    cached_img = os.path.join(context.nochanges_dir, "pronzeos.img")
    img_stat = "OK" if os.path.exists(cached_img) else "Missing"

    saved_master = ""
    saved_hash_file = os.path.join(context.nochanges_dir, "master.hash")
    if os.path.exists(saved_hash_file):
        with open(saved_hash_file, 'r') as f:
            saved_master = f.read().strip()[:8]

    # Calculate current master hash prefix
    curr_master_calc = "None"
    try:
        current_hashes = compute_all_hashes(context)
        inputs = [
            current_hashes["kernel_dir"],
            current_hashes["sdk_dir"],
            current_hashes["daemon_dir"],
            current_hashes["s6_dir"],
            current_hashes["test_dir"],
            current_hashes["profiles_dir"],
            current_hashes["build_pipeline_dir"],
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
        curr_master_calc = hashlib.sha256("-".join(inputs).encode('utf-8')).hexdigest()[:8]
    except Exception:
        pass

    # Helper function to check if a specific stage has a valid cache
    def check_stage_cache(stage_name, expected_hash):
        if not expected_hash or expected_hash == "None":
            return "Mismatch"
        hash_file = os.path.join(context.nochanges_dir, f"{stage_name}.hash")
        done_file = os.path.join(context.nochanges_dir, f"{stage_name}.done")
        if os.path.exists(hash_file) and os.path.exists(done_file):
            with open(hash_file, 'r') as f:
                saved_val = f.read().strip()
            if saved_val == expected_hash:
                return "Match"
        return "Mismatch"

    # Compute expected hashes for specific components
    # 1. Kernel hash
    linux_tar_hash = get_file_hash(os.path.join(context.download_dir, f"linux-{linux_ver}.tar.xz"))
    expected_kernel_hash = hashlib.sha256(f"{linux_ver}-{linux_tar_hash}".encode('utf-8')).hexdigest() if linux_tar_hash else "None"
    kernel_cache_match = check_stage_cache("CompileKernel", expected_kernel_hash)

    # 2. BusyBox hash
    busybox_tar_hash = get_file_hash(os.path.join(context.download_dir, f"busybox-{busybox_ver}.tar.bz2"))
    expected_busybox_hash = hashlib.sha256(f"{busybox_ver}-{busybox_tar_hash}".encode('utf-8')).hexdigest() if busybox_tar_hash else "None"
    busybox_cache_match = check_stage_cache("CompileBusybox", expected_busybox_hash)

    # 3. s6 hash
    skalibs_ver = context.config.get('SKALIBS_VERSION', '0.0.0.0')
    execline_ver = context.config.get('EXECLINE_VERSION', '0.0.0.0')
    skalibs_tar_hash = get_file_hash(os.path.join(context.download_dir, f"skalibs-{skalibs_ver}.tar.gz"))
    execline_tar_hash = get_file_hash(os.path.join(context.download_dir, f"execline-{execline_ver}.tar.gz"))
    s6_tar_hash = get_file_hash(os.path.join(context.download_dir, f"s6-{s6_ver}.tar.gz"))
    expected_s6_hash = hashlib.sha256(f"{skalibs_ver}-{execline_ver}-{s6_ver}-{skalibs_tar_hash}-{execline_tar_hash}-{s6_tar_hash}".encode('utf-8')).hexdigest() if (skalibs_tar_hash and execline_tar_hash and s6_tar_hash) else "None"
    s6_cache_match = check_stage_cache("CompileS6", expected_s6_hash)

    # Master disk image match
    master_done_file = os.path.join(context.nochanges_dir, "master.done")
    master_cache_match = "Match" if (saved_master and saved_master == curr_master_calc and os.path.exists(master_done_file)) else "Mismatch"

    table = [
        ("Component", "Status / Version", "Hash (SHA256)", "Cache Match"),
        ("--------------------+", "------------------+", "----------------+", "------------+"),
        ("Linux Kernel", f"{linux_ver} ({linux_stat})", linux_hash, kernel_cache_match),
        ("BusyBox", f"{busybox_ver} ({busybox_stat})", busybox_hash, busybox_cache_match),
        ("s6 Supervision", f"{s6_ver} ({s6_stat})", s6_hash, s6_cache_match),
        ("Master Disk Image", img_stat, saved_master or "None", master_cache_match),
        ("Current Master Calc", "Calculated", curr_master_calc, master_cache_match)
    ]

    for row in table:
        print(f"| {row[0]:<18} | {row[1]:<16} | {row[2]:<14} | {row[3]:<10} |")
    Logger.log_section("")

    # Display detected changes in CLI status report
    generate_diff_report(context)
    if detected_changes:
        Logger.log_warn("Detected Hash Mismatches:")
        for change in detected_changes:
            Logger.log_plain(f"  - {change['component']}: {change['old']} -> {change['new']}")
    else:
        Logger.log_success("All hashes match! No changes detected in workspace sources.")
    Logger.log_section("")

    # Display compilation analyzer report in console
    try:
        analyze_compilation_files(context)
        Logger.log_section("          Compilation Analyzer Report          ")
        print(f"| {'Stage':<25} | {'Status':<15} | {'Files to Compile':<18} |")
        print(f"| {'-------------------------':<25} | {'---------------':<15} | {'------------------':<18} |")
        for stage, info in compilation_analyzer_data.items():
            if stage == "total_files":
                continue
            status_str = info["status"]
            files_str = f"{info['files']}"
            print(f"| {stage:<25} | {status_str:<15} | {files_str:<18} |")
        print(f"| {'-------------------------':<25} | {'---------------':<15} | {'------------------':<18} |")
        print(f"| {'Total Files to Compile':<25} | {'':<15} | {compilation_analyzer_data['total_files']:<18} |")
        Logger.log_section("")
    except Exception as e:
        Logger.log_error(f"Failed to run compilation analyzer: {e}")

    # Wait countdown
    # for i in range(3, 0, -1):
    #     Logger.log_step(f"Starting build pipeline in {i} seconds...")
    #     time.sleep(1)