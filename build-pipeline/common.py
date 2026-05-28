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

# Authentication and env state
PORTAL_PASSWORD = "root"

# Background worker queues and QEMU guest state
build_queue = queue.Queue()
build_active = False
build_completed = False
total_build_time = 0.0
final_status_text = ""

global_context = None
global_pipeline = None

# QEMU process variables
qemu_process = None
qemu_log_buffer = ""
qemu_lock = threading.Lock()
qemu_reader_thread = None

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

        /* Frosted Glass Console Box with Grain Overlay */
        .console-box {
            border: 3px solid var(--border-color);
            background-color: rgba(255, 255, 255, 0.75);
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.06'/%3E%3C/svg%3E");
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
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
        .status-dot.idle { background: #adb5bd; }

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

        /* Node Card with Grain Overlay */
        .node-card {
            border: 3px solid var(--border-color);
            background-color: rgba(255, 255, 255, 0.75);
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.06'/%3E%3C/svg%3E");
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
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
            background-color: #fff9db !important;
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

        /* Administrative Actions Styling */
        .control-btn {
            border: 3px solid var(--border-color);
            background: #ffffff;
            color: var(--text-color);
            font-family: 'JetBrains Mono', monospace;
            font-weight: 800;
            font-size: 0.8rem;
            padding: 0.45rem 0.8rem;
            cursor: pointer;
            box-shadow: 2px 2px 0px var(--border-color);
            transition: transform 0.1s, box-shadow 0.1s;
            text-transform: uppercase;
        }
        .control-btn:hover {
            background: #f1f3f5;
        }
        .control-btn:active {
            transform: translate(1px, 1px);
            box-shadow: 1px 1px 0px var(--border-color);
        }
        .control-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none !important;
            box-shadow: 2px 2px 0px var(--border-color) !important;
        }
        .control-btn.warning {
            border-color: #f59f00;
            color: #f59f00;
            box-shadow: 2px 2px 0px #f59f00;
        }
        .control-btn.warning:hover {
            background: #fff9db;
        }
        .control-btn.danger {
            border-color: #f03e3e;
            color: #f03e3e;
            box-shadow: 2px 2px 0px #f03e3e;
        }
        .control-btn.danger:hover {
            background: #fff5f5;
        }
        .control-btn.accent {
            border-color: #1a1a1a;
            background: #1a1a1a;
            color: #ffffff;
            box-shadow: 2px 2px 0px var(--border-color);
        }
        .control-btn.accent:hover {
            background: #2b2b2b;
        }

        /* Glassmorphism Password modal */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(0, 0, 0, 0.45);
            backdrop-filter: blur(15px);
            -webkit-backdrop-filter: blur(15px);
            z-index: 1000;
            display: flex;
            justify-content: center;
            align-items: center;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.25s ease;
        }
        .modal-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }
        .modal-box {
            border: 3px solid var(--border-color);
            background-color: rgba(255, 255, 255, 0.85);
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.06'/%3E%3C/svg%3E");
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            box-shadow: 6px 6px 0px var(--border-color);
            padding: 1.5rem;
            width: 380px;
            position: relative;
        }
        .modal-title {
            font-family: 'JetBrains Mono', monospace;
            font-weight: 800;
            font-size: 0.95rem;
            border-bottom: 3px solid var(--border-color);
            margin: -1.5rem -1.5rem 1rem -1.5rem;
            padding: 0.6rem 1.5rem;
            background: #1a1a1a;
            color: #ffffff;
            text-transform: uppercase;
        }
        .modal-input {
            width: 100%;
            box-sizing: border-box;
            border: 3px solid var(--border-color);
            padding: 0.5rem 0.75rem;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            margin-bottom: 0.75rem;
            outline: none;
            background: #faf9f6;
        }
        .modal-input:focus {
            background: #ffffff;
        }

        /* QEMU Terminal Console style */
        .terminal-view {
            border: 3px solid var(--border-color);
            background: #0c0c0c;
            color: #4af626;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem;
            height: 350px;
            overflow-y: auto;
            padding: 0.75rem;
            box-shadow: inset 0px 0px 10px rgba(0, 0, 0, 0.8);
            line-height: 1.3;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .term-tstamp { color: #868e96; font-weight: bold; }
        .term-check { color: #37b24d; font-weight: bold; }
        .term-check-text { color: #8ce99a; }
        .term-error { color: #fa5252; font-weight: bold; }
        .term-error-text { color: #ffc9c9; }
        .term-warn { color: #fcc419; font-weight: bold; }
        .term-warn-text { color: #ffe066; }
        .term-info { color: #339af0; font-weight: bold; }
        .term-step { color: #15aabf; font-weight: bold; }
    </style>
</head>
<body>
    <!-- Password overlay modal -->
    <div class="modal-overlay" id="auth-modal">
        <div class="modal-box">
            <div class="modal-title">Authentication Required</div>
            <p style="font-size: 0.85rem; margin-top: 0; color: #495057;">Enter your Portal Password to proceed with this operation.</p>
            <input type="password" class="modal-input" id="auth-pw-input" placeholder="Password" onkeydown="if(event.key==='Enter') submitAuth()" />
            <div style="margin-bottom: 1rem;">
                <label style="font-size: 0.8rem; display: flex; align-items: center; gap: 0.25rem; font-weight: bold; cursor: pointer;">
                    <input type="checkbox" id="auth-remember-cb" checked /> Remember password in this browser
                </label>
            </div>
            <div id="auth-error-msg" style="color: #f03e3e; font-size: 0.8rem; font-weight: bold; margin-bottom: 1rem;"></div>
            <div style="display: flex; justify-content: flex-end; gap: 0.5rem;">
                <button class="control-btn" onclick="hideAuthModal()">Cancel</button>
                <button class="control-btn accent" onclick="submitAuth()">Verify</button>
            </div>
        </div>
    </div>

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
                <div class="console-box-title">ADMIN CONTROLS</div>
                <div style="display: flex; flex-direction: column; gap: 0.5rem;">
                    <button class="control-btn accent" onclick="triggerRebuild()">REBUILD PIPELINE</button>
                    <button class="control-btn warning" onclick="triggerClean()">CLEAN WORKSPACE</button>
                    <button class="control-btn danger" onclick="triggerFclean()">FCLEAN (RESET ALL)</button>
                    <label style="font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; font-weight: bold; display: flex; align-items: center; gap: 0.25rem; margin-top: 0.25rem; cursor: pointer; color: #495057;">
                        <input type="checkbox" id="fclean-kill-tars-cb" /> Also delete downloaded tarballs
                    </label>
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

        <!-- Right Side: DAG and QEMU Console -->
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

                    <!-- Level 5.5 (New stage) -->
                    <div class="stage-row">
                        <div class="node-card pending" id="node-CopyConfigurationSetup" onclick="selectStage('CopyConfigurationSetup')">
                            <div class="node-name">Copy Setup Files</div>
                            <span class="node-status-label" id="status-CopyConfigurationSetup">Pending</span>
                            <span class="node-duration" id="duration-CopyConfigurationSetup"></span>
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

            <!-- QEMU Console Container -->
            <div class="console-box" id="qemu-console-box">
                <div class="console-box-title">
                    <span>QEMU GUEST CONSOLE</span>
                    <span id="qemu-status-text" style="color: #adb5bd;">STOPPED</span>
                </div>
                
                <div style="margin-bottom: 1rem; display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;">
                    <button class="control-btn" id="qemu-start-btn" onclick="triggerQemuStart()">START QEMU</button>
                    <button class="control-btn danger" id="qemu-stop-btn" onclick="triggerQemuStop()" disabled>STOP QEMU</button>
                    <a id="download-btn-link" href="#" style="text-decoration: none; margin-left: auto;">
                        <button class="control-btn accent" id="download-image-btn" onclick="triggerDownload(event)">DOWNLOAD IMAGE</button>
                    </a>
                </div>

                <div id="qemu-log-terminal" class="terminal-view">
                    <div style="color: #666; font-style: italic; font-family: 'JetBrains Mono', monospace;">Console inactive. Click 'START QEMU' to boot the PronzeOS guest VM.</div>
                </div>
                
                <div id="qemu-input-container" style="display: flex; margin-top: 0.75rem; border: 3px solid var(--border-color); display: none;">
                    <span style="font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; font-weight: bold; background: #1a1a1a; color: white; padding: 0.45rem 0.75rem;">guest$</span>
                    <input type="text" id="qemu-input-field" placeholder="Type command to guest shell and press Enter..." style="flex: 1; border: none; padding: 0.45rem 0.75rem; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; outline: none; background: #faf9f6;" onkeydown="sendQemuInput(event)" />
                </div>
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

        // PasswordWall / Authentication Flow
        let pendingAction = null;
        
        function getSavedPassword() {
            return localStorage.getItem("portal_password") || "";
        }
        
        function setSavedPassword(pw, remember) {
            if (remember) {
                localStorage.setItem("portal_password", pw);
            } else {
                localStorage.removeItem("portal_password");
            }
        }
        
        function showAuthModal(callback) {
            pendingAction = callback;
            document.getElementById("auth-pw-input").value = "";
            document.getElementById("auth-error-msg").textContent = "";
            document.getElementById("auth-modal").classList.add("active");
            document.getElementById("auth-pw-input").focus();
        }
        
        function hideAuthModal() {
            document.getElementById("auth-modal").classList.remove("active");
            pendingAction = null;
        }
        
        function submitAuth() {
            const pw = document.getElementById("auth-pw-input").value;
            const remember = document.getElementById("auth-remember-cb").checked;
            
            fetch("/api/auth", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password: pw })
            })
            .then(res => {
                if (res.ok) {
                    setSavedPassword(pw, remember);
                    const cb = pendingAction;
                    hideAuthModal();
                    if (cb) cb(pw);
                } else {
                    document.getElementById("auth-error-msg").textContent = "Invalid password. Try again.";
                }
            })
            .catch(err => {
                document.getElementById("auth-error-msg").textContent = "Connection error: " + err;
            });
        }
        
        function performProtectedAction(actionFn) {
            const savedPw = getSavedPassword();
            if (savedPw) {
                actionFn(savedPw, (err) => {
                    if (err && err.status === 401) {
                        localStorage.removeItem("portal_password");
                        showAuthModal(actionFn);
                    }
                });
            } else {
                showAuthModal(actionFn);
            }
        }

        // Admin Buttons Triggers
        function triggerClean() {
            performProtectedAction((pw, onDone) => {
                fetch("/api/clean", {
                    method: "POST",
                    headers: { "X-Portal-Password": pw }
                })
                .then(res => {
                    if (res.status === 401) { onDone({status: 401}); return; }
                    if (res.ok) {
                        alert("Clean completed!");
                    } else {
                        alert("Clean failed!");
                    }
                    onDone();
                })
                .catch(err => { alert("Clean failed: " + err); onDone(); });
            });
        }

        function triggerFclean() {
            const killTars = document.getElementById("fclean-kill-tars-cb").checked;
            performProtectedAction((pw, onDone) => {
                fetch("/api/fclean", {
                    method: "POST",
                    headers: { 
                        "X-Portal-Password": pw,
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({ kill_tars: killTars })
                })
                .then(res => {
                    if (res.status === 401) { onDone({status: 401}); return; }
                    if (res.ok) {
                        alert("Full clean (FClean) completed!");
                    } else {
                        alert("FClean failed!");
                    }
                    onDone();
                })
                .catch(err => { alert("FClean failed: " + err); onDone(); });
            });
        }

        function triggerRebuild() {
            performProtectedAction((pw, onDone) => {
                fetch("/api/rebuild", {
                    method: "POST",
                    headers: { "X-Portal-Password": pw }
                })
                .then(res => {
                    if (res.status === 401) { onDone({status: 401}); return; }
                    if (res.ok) {
                        // Success: State will update dynamically via SSE
                    } else {
                        res.json().then(d => alert("Rebuild failed: " + (d.error || "unknown")));
                    }
                    onDone();
                })
                .catch(err => { alert("Rebuild failed: " + err); onDone(); });
            });
        }

        function triggerDownload(e) {
            e.preventDefault();
            performProtectedAction((pw, onDone) => {
                const link = document.getElementById("download-btn-link");
                link.href = `/api/download?password=${encodeURIComponent(pw)}`;
                const a = document.createElement("a");
                a.href = link.href;
                a.download = "pronzeos.img";
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                onDone();
            });
        }

        // QEMU Console guest controls
        function triggerQemuStart() {
            performProtectedAction((pw, onDone) => {
                const startBtn = document.getElementById("qemu-start-btn");
                startBtn.disabled = true;
                startBtn.textContent = "LAUNCHING...";
                
                fetch("/api/qemu/start", {
                    method: "POST",
                    headers: { "X-Portal-Password": pw }
                })
                .then(res => {
                    if (res.status === 401) { onDone({status: 401}); return; }
                    res.json().then(data => {
                        if (!res.ok) {
                            alert("Failed to start QEMU: " + data.message);
                            startBtn.disabled = false;
                            startBtn.textContent = "START QEMU";
                        }
                    });
                    onDone();
                })
                .catch(err => {
                    alert("Error starting QEMU: " + err);
                    startBtn.disabled = false;
                    startBtn.textContent = "START QEMU";
                    onDone();
                });
            });
        }

        function triggerQemuStop() {
            performProtectedAction((pw, onDone) => {
                fetch("/api/qemu/stop", {
                    method: "POST",
                    headers: { "X-Portal-Password": pw }
                })
                .then(res => {
                    if (res.status === 401) { onDone({status: 401}); return; }
                    if (!res.ok) {
                        res.json().then(data => alert("Failed to stop QEMU: " + data.message));
                    }
                    onDone();
                })
                .catch(err => {
                    alert("Error stopping QEMU: " + err);
                    onDone();
                });
            });
        }

        function sendQemuInput(e) {
            if (e.key === "Enter") {
                const inputField = document.getElementById("qemu-input-field");
                const val = inputField.value + "\\n";
                inputField.value = "";
                
                const pw = getSavedPassword();
                if (!pw) {
                    alert("Not authenticated. Please click Start QEMU to authenticate.");
                    return;
                }
                
                fetch("/api/qemu/input", {
                    method: "POST",
                    headers: { 
                        "X-Portal-Password": pw,
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({ input: val })
                })
                .catch(err => {
                    console.error("Error sending input: " + err);
                });
            }
        }

        // QEMU Log viewer parser
        function parseLogLine(text) {
            let escaped = text
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;");

            const timestampRegex = /^(\s*\[\s*\d+\.\d+\s*\])(.*)$/;
            const checkRegex = /^(\s*\[\s*✔\s*\])(.*)$/;
            const crossRegex = /^(\s*\[\s*x\s*\])(.*)$/;
            const warnRegex = /^(\s*\[\s*!\s*\])(.*)$/;
            const infoRegex = /^(\s*\[\s*i\s*\])(.*)$/;
            const stepRegex = /^(\s*\[\s*(\+|•)\s*\])(.*)$/;

            if (timestampRegex.test(escaped)) {
                return escaped.replace(timestampRegex, '<span class="term-tstamp">$1</span>$2');
            } else if (checkRegex.test(escaped)) {
                return escaped.replace(checkRegex, '<span class="term-check">$1</span><span class="term-check-text">$2</span>');
            } else if (crossRegex.test(escaped)) {
                return escaped.replace(crossRegex, '<span class="term-error">$1</span><span class="term-error-text">$2</span>');
            } else if (warnRegex.test(escaped)) {
                return escaped.replace(warnRegex, '<span class="term-warn">$1</span><span class="term-warn-text">$2</span>');
            } else if (infoRegex.test(escaped)) {
                return escaped.replace(infoRegex, '<span class="term-info">$1</span>$2');
            } else if (stepRegex.test(escaped)) {
                return escaped.replace(stepRegex, '<span class="term-step">$1</span>$2');
            }
            return escaped;
        }

        let accumulatedQemuLogs = "";
        
        function appendToTerminal(text) {
            const term = document.getElementById("qemu-log-terminal");
            accumulatedQemuLogs += text;
            
            if (accumulatedQemuLogs.length > 100000) {
                accumulatedQemuLogs = accumulatedQemuLogs.substring(accumulatedQemuLogs.length - 50000);
            }
            
            const lines = accumulatedQemuLogs.split("\\n");
            let html = "";
            lines.forEach((line, idx) => {
                if (idx === lines.length - 1) {
                    html += parseLogLine(line);
                } else {
                    html += parseLogLine(line) + "\\n";
                }
            });
            term.innerHTML = html;
            term.scrollTop = term.scrollHeight;
        }

        // SSE Connection
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
            
            if (payload.build_start_time && payload.build_active) {
                startTimer(payload.build_start_time, payload.server_current_time);
                updateGlobalStatus("BUILDING", "building");
            } else if (payload.build_completed) {
                stopTimer();
                updateGlobalStatus(payload.final_status_text ? payload.final_status_text.toUpperCase() : "COMPLETE", payload.final_status_text && payload.final_status_text.toLowerCase() === "failed" ? "failed" : "success");
            } else {
                updateGlobalStatus("IDLE", "idle");
            }

            // Sync QEMU UI state
            if (payload.qemu_active) {
                document.getElementById("qemu-status-text").textContent = "RUNNING";
                document.getElementById("qemu-status-text").style.color = "#37b24d";
                document.getElementById("qemu-start-btn").disabled = true;
                document.getElementById("qemu-stop-btn").disabled = false;
                document.getElementById("qemu-input-container").style.display = "flex";
            } else {
                document.getElementById("qemu-status-text").textContent = "STOPPED";
                document.getElementById("qemu-status-text").style.color = "#adb5bd";
                document.getElementById("qemu-start-btn").disabled = false;
                document.getElementById("qemu-stop-btn").disabled = true;
                document.getElementById("qemu-input-container").style.display = "none";
            }
            
            updateBuildProgress(successCount, totalCount);
        });
        
        evtSource.addEventListener("update", (e) => {
            const data = JSON.parse(e.data);
            updateNode(data.name, data.status, data.details, data.elapsed);
            
            if (data.status === "Running") {
                if (data.build_start_time) {
                    startTimer(data.build_start_time, data.server_current_time);
                }
                updateGlobalStatus("BUILDING", "building");
                selectStage(data.name);
            }
            
            let cards = document.querySelectorAll(".node-card");
            let successCount = 0;
            cards.forEach(card => {
                if (card.classList.contains("success") || card.classList.contains("skipped")) {
                    successCount++;
                }
            });
            updateBuildProgress(successCount, cards.length);
        });

        evtSource.addEventListener("qemu_started", (e) => {
            document.getElementById("qemu-status-text").textContent = "RUNNING";
            document.getElementById("qemu-status-text").style.color = "#37b24d";
            document.getElementById("qemu-start-btn").disabled = true;
            document.getElementById("qemu-start-btn").textContent = "START QEMU";
            document.getElementById("qemu-stop-btn").disabled = false;
            document.getElementById("qemu-input-container").style.display = "flex";
            document.getElementById("qemu-log-terminal").innerHTML = '<div style="color: #37b24d; font-weight: bold;">[+] Connecting to serial console...</div>';
            document.getElementById("qemu-input-field").focus();
        });

        evtSource.addEventListener("qemu_stopped", (e) => {
            document.getElementById("qemu-status-text").textContent = "STOPPED";
            document.getElementById("qemu-status-text").style.color = "#adb5bd";
            document.getElementById("qemu-start-btn").disabled = false;
            document.getElementById("qemu-stop-btn").disabled = true;
            document.getElementById("qemu-input-container").style.display = "none";
        });

        evtSource.addEventListener("qemu_log", (e) => {
            const char = JSON.parse(e.data);
            appendToTerminal(char);
        });

        evtSource.addEventListener("qemu_initial_logs", (e) => {
            const logs = JSON.parse(e.data);
            if (logs) {
                accumulatedQemuLogs = "";
                appendToTerminal(logs);
            }
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
            if (statusEl) {
                statusEl.textContent = status === "Success" ? "Complete" : status;
            }
            
            const durEl = document.getElementById("duration-" + name);
            if (durEl) {
                if (elapsed !== null) {
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
        import urllib.parse
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        if path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
        elif path == '/events':
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
                "server_current_time": time.time(),
                "build_completed": build_completed,
                "build_active": build_active,
                "qemu_active": qemu_process is not None and qemu_process.poll() is None
            }
            initial_state_msg = f"event: state\ndata: {json.dumps(state_payload)}\n\n"
            with log_lock:
                initial_logs_msg = f"event: initial_logs\ndata: {json.dumps(log_buffers)}\n\n"
            with qemu_lock:
                initial_qemu_logs_msg = f"event: qemu_initial_logs\ndata: {json.dumps(qemu_log_buffer)}\n\n"
            hashes_payload = {
                "changes": detected_changes,
                "current_hashes": current_hashes_prefix
            }
            initial_hashes_msg = f"event: hashes_report\ndata: {json.dumps(hashes_payload)}\n\n"
            analyzer_msg = f"event: analyzer_report\ndata: {json.dumps(compilation_analyzer_data)}\n\n"
            
            # If the build has already completed, send a total_report immediately upon connection
            total_report_msg = ""
            if build_completed:
                rows = []
                for node_name in node_states:
                    state = node_states.get(node_name, {})
                    elapsed = state.get("elapsed")
                    elapsed_str = f"{elapsed:.2f}s" if elapsed is not None else ("skipped" if state.get("status") == "Skipped" else "0.00s")
                    rows.append({"name": node_name, "status": state.get("status"), "elapsed": elapsed_str})
                
                payload = {
                    "total_time": f"{total_build_time:.2f}s",
                    "status": final_status_text,
                    "report_table": rows
                }
                total_report_msg = f"event: total_report\ndata: {json.dumps(payload)}\n\n"

            try:
                self.wfile.write(initial_state_msg.encode('utf-8'))
                self.wfile.write(initial_logs_msg.encode('utf-8'))
                self.wfile.write(initial_qemu_logs_msg.encode('utf-8'))
                self.wfile.write(initial_hashes_msg.encode('utf-8'))
                self.wfile.write(analyzer_msg.encode('utf-8'))
                if total_report_msg:
                    self.wfile.write(total_report_msg.encode('utf-8'))
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
                
        elif path == '/api/download':
            query_params = urllib.parse.parse_qs(parsed_url.query)
            pw_param = query_params.get('password', [None])[0]
            password_header = self.headers.get("X-Portal-Password")
            
            if pw_param != PORTAL_PASSWORD and password_header != PORTAL_PASSWORD:
                self.send_response(401)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write("Unauthorized".encode('utf-8'))
                return
                
            global global_context
            if not global_context:
                self.send_error(500, "Context not initialized")
                return
                
            img_path = os.path.join(global_context.workspace_dir, "output", "pronzeos.img")
            if not os.path.exists(img_path):
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write("File not found. Please build the pipeline first!".encode('utf-8'))
                return
                
            try:
                file_size = os.path.getsize(img_path)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "attachment; filename=pronzeos.img")
                self.send_header("Content-Length", str(file_size))
                self.end_headers()
                
                with open(img_path, 'rb') as f:
                    shutil.copyfileobj(f, self.wfile, length=64*1024)
            except Exception as e:
                Logger.log_error(f"Error streaming file: {e}")
        else:
            self.send_error(404)

    def do_POST(self):
        global global_context, global_pipeline, qemu_process
        # 1. Authenticate with header
        password_header = self.headers.get("X-Portal-Password")
        
        # Support authenticating via POST /api/auth
        if self.path == '/api/auth':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                pw = data.get("password")
                if pw == PORTAL_PASSWORD:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
                    return
            except Exception:
                pass
            
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid password"}).encode('utf-8'))
            return
            
        # For other endpoints, authenticate with header
        if password_header != PORTAL_PASSWORD:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized"}).encode('utf-8'))
            return
            
        # Handle endpoints
        if self.path == '/api/clean':
            if global_context:
                Logger.log_info("Clean workspace requested from web view")
                subprocess.run(os.path.join(global_context.workspace_dir, "scripts", "clean.sh"), shell=True)
                analyze_compilation_files(global_context)
                generate_diff_report(global_context)
                broadcast_status(global_context)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            else:
                self.send_error(500, "Context not initialized")
                
        elif self.path == '/api/fclean':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            kill_tars = False
            try:
                data = json.loads(body)
                kill_tars = data.get("kill_tars", False)
            except Exception:
                pass
                
            if global_context:
                Logger.log_info(f"FClean workspace requested from web view (kill_tars: {kill_tars})")
                cmd = os.path.join(global_context.workspace_dir, "scripts", "fclean.sh")
                if kill_tars:
                    cmd += " -k"
                subprocess.run(cmd, shell=True)
                analyze_compilation_files(global_context)
                generate_diff_report(global_context)
                broadcast_status(global_context)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            else:
                self.send_error(500, "Context not initialized")
                
        elif self.path == '/api/rebuild':
            if global_context and global_pipeline:
                if build_active:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Build already active"}).encode('utf-8'))
                    return
                Logger.log_info("Rebuild pipeline requested from web view")
                # Trigger rebuild
                build_queue.put((global_context, global_pipeline))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            else:
                self.send_error(500, "Context/Pipeline not initialized")
                
        elif self.path == '/api/qemu/start':
            if global_context:
                success, msg = start_qemu_guest(global_context.workspace_dir)
                self.send_response(200 if success else 400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok" if success else "error", "message": msg}).encode('utf-8'))
            else:
                self.send_error(500, "Context not initialized")
                
        elif self.path == '/api/qemu/stop':
            success, msg = stop_qemu_guest()
            self.send_response(200 if success else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok" if success else "error", "message": msg}).encode('utf-8'))
            
        elif self.path == '/api/qemu/input':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                inp = data.get("input", "")
                if qemu_process and qemu_process.poll() is None:
                    qemu_process.stdin.write(inp)
                    qemu_process.stdin.flush()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
                    return
            except Exception as e:
                Logger.log_error(f"Error sending keypress to QEMU: {e}")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "QEMU not running or invalid input"}).encode('utf-8'))
        else:
            self.send_error(404)

def start_http_server(port=8000):
    if no_view_flag:
        return
    server = http.server.ThreadingHTTPServer(('0.0.0.0', port), SSEHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    Logger.log_info(f"HTML GUI visualization server started on http://localhost:{port}")

def load_portal_password(workspace_dir):
    global PORTAL_PASSWORD
    env_path = os.path.join(workspace_dir, ".env")
    default_pw = "root"
    if not os.path.exists(env_path):
        try:
            with open(env_path, "w") as f:
                f.write("# PronzeOS Portal Authentication Settings\n")
                f.write(f"PORTAL_PASSWORD={default_pw}\n")
            Logger.log_warn("Generated default .env file with PORTAL_PASSWORD=root. Please change the password in .env to secure your public interface!")
            PORTAL_PASSWORD = default_pw
            return
        except Exception as e:
            Logger.log_warn(f"Failed to generate .env file: {e}")
            PORTAL_PASSWORD = default_pw
            return
    
    # Read existing .env
    password = default_pw
    try:
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "PORTAL_PASSWORD":
                        password = v.strip().strip('"').strip("'")
                        break
    except Exception as e:
        Logger.log_warn(f"Failed to read .env file: {e}")
    
    if password == default_pw:
        Logger.log_warn("PORTAL_PASSWORD is set to default 'root' in .env. Please change the password to secure your public interface!")
    
    PORTAL_PASSWORD = password

def broadcast_status(context):
    if no_view_flag:
        return
    
    # 1. State
    state_payload = {
        "nodes": node_states,
        "build_start_time": build_start_time,
        "server_current_time": time.time(),
        "build_completed": build_completed,
        "build_active": build_active,
        "qemu_active": qemu_process is not None and qemu_process.poll() is None
    }
    state_msg = f"event: state\ndata: {json.dumps(state_payload)}\n\n"
    
    # 2. Hashes
    hashes_payload = {
        "changes": detected_changes,
        "current_hashes": current_hashes_prefix
    }
    hashes_msg = f"event: hashes_report\ndata: {json.dumps(hashes_payload)}\n\n"
    
    # 3. Analyzer
    analyzer_msg = f"event: analyzer_report\ndata: {json.dumps(compilation_analyzer_data)}\n\n"
    
    for q in list(clients):
        try:
            q.put(state_msg)
            q.put(hashes_msg)
            q.put(analyzer_msg)
        except Exception:
            pass

def find_ovmf_firmware():
    paths = [
        "/usr/share/OVMF/OVMF_CODE.fd",
        "/usr/share/ovmf/OVMF.fd",
        "/usr/share/qemu/OVMF.fd",
        "/opt/homebrew/share/qemu/edk2-x86_64-code.fd",
        "/opt/homebrew/share/qemu/OVMF.fd",
    ]
    for p in paths:
        if os.path.isfile(p):
            return p
    # Search fallback
    for root_dir in ["/usr/share/OVMF", "/usr/share/ovmf", "/usr/share/qemu", "/opt/homebrew/share/qemu"]:
        if os.path.isdir(root_dir):
            for root, dirs, files in os.walk(root_dir):
                for f in files:
                    if f in ["edk2-x86_64-code.fd", "OVMF.fd", "OVMF_CODE.fd"]:
                        return os.path.join(root, f)
    return None

def qemu_reader():
    global qemu_process, qemu_log_buffer
    while True:
        proc = qemu_process
        if not proc or proc.poll() is not None:
            break
        try:
            char = proc.stdout.read(1)
            if not char:
                break
            with qemu_lock:
                qemu_log_buffer += char
                if len(qemu_log_buffer) > 200000:
                    qemu_log_buffer = qemu_log_buffer[-100000:]
            msg = f"event: qemu_log\ndata: {json.dumps(char)}\n\n"
            for q in list(clients):
                try:
                    q.put(msg)
                except Exception:
                    pass
        except Exception:
            break
    
    # Notify clients QEMU stopped
    stop_msg = f"event: qemu_stopped\ndata: {{}}\n\n"
    for q in list(clients):
        try:
            q.put(stop_msg)
        except Exception:
            pass

def start_qemu_guest(workspace_dir):
    global qemu_process, qemu_log_buffer, qemu_reader_thread
    if qemu_process and qemu_process.poll() is None:
        return True, "QEMU is already running."
    
    img_path = os.path.join(workspace_dir, "output", "pronzeos.img")
    if not os.path.exists(img_path):
        return False, f"Disk image not found at {img_path}. Please build first!"
        
    ovmf_path = find_ovmf_firmware()
    
    qemu_cmd = [
        "qemu-system-x86_64",
        "-m", "1G",
        "-hda", img_path,
        "-display", "none",
        "-serial", "stdio"
    ]
    if ovmf_path:
        qemu_cmd.extend(["-drive", f"if=pflash,format=raw,unit=0,file={ovmf_path},readonly=on"])
        Logger.log_info(f"Using UEFI firmware: {ovmf_path}")
    else:
        Logger.log_warn("No UEFI firmware found. systemd-boot inside guest might fail.")

    try:
        qemu_log_buffer = ""
        qemu_process = subprocess.Popen(
            qemu_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        qemu_reader_thread = threading.Thread(target=qemu_reader, daemon=True)
        qemu_reader_thread.start()
        
        msg = f"event: qemu_started\ndata: {{}}\n\n"
        for q in list(clients):
            try:
                q.put(msg)
            except Exception:
                pass
                
        return True, "QEMU started successfully."
    except Exception as e:
        return False, f"Failed to start QEMU: {e}"

def stop_qemu_guest():
    global qemu_process
    if not qemu_process or qemu_process.poll() is not None:
        return False, "QEMU is not running."
    
    try:
        qemu_process.terminate()
        try:
            qemu_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            qemu_process.kill()
        qemu_process = None
        return True, "QEMU stopped successfully."
    except Exception as e:
        qemu_process = None
        return False, f"Error stopping QEMU: {e}"

def build_worker():
    global build_active, build_completed, total_build_time, final_status_text, build_start_time
    while True:
        task = build_queue.get()
        if task is None:
            build_queue.task_done()
            break
        
        context, pipeline = task
        build_active = True
        try:
            # Reset build state
            build_completed = False
            total_build_time = 0.0
            final_status_text = ""
            build_start_time = time.time()
            
            # Reset node states in the UI
            for node in pipeline.execution_order:
                node_states[node.name] = {"status": "Pending", "details": "Waiting in queue...", "elapsed": None}
                with log_lock:
                    log_buffers[node.name] = ""
            
            # Trigger initial broadcast
            broadcast_status(context)
            
            # Run the build
            pipeline.execute(context)
            
        except Exception as e:
            Logger.log_error(f"Error in background build: {e}")
        finally:
            build_active = False
            broadcast_status(context)
            build_queue.task_done()

# Start background build worker thread
build_thread = threading.Thread(target=build_worker, daemon=True)
build_thread.start()

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

        global global_context
        global_context = self
        load_portal_password(self.workspace_dir)

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
        global current_stage_name, build_start_time, global_pipeline, build_completed, total_build_time, final_status_text
        global_pipeline = self
        overall_start_t = time.time()
        build_start_time = overall_start_t
        context.overall_start_t = overall_start_t
        context.pipeline = self
        context.skip_remaining = False
        
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
                
                total_build_time = time.time() - overall_start_t
                final_status_text = "Failed"
                build_completed = True
                send_total_report(total_build_time, "Failed")
                return

        total_time = time.time() - overall_start_t
        Logger.log_section("          Build Completion Report          ")
        for node in self.execution_order:
            state = node_states.get(node.name, {})
            duration = state.get("elapsed")
            duration_str = f"{duration:.2f}s" if duration is not None else ("Skipped" if state.get("status") == "Skipped" else "0.00s")
            Logger.log_plain(f"  - {node.name:<25}: {state.get('status'):<10} ({duration_str})")
        Logger.log_plain(f"\n  [✔] Total Build Duration: {total_time:.2f}s")
        Logger.log_section("")

        total_build_time = total_time
        final_status_text = "Complete"
        build_completed = True

        # Save all current hashes to hashes.json
        hashes_file = os.path.join(context.nochanges_dir, "hashes.json")
        try:
            current_hashes = compute_all_hashes(context)
            with open(hashes_file, 'w', encoding='utf-8') as f:
                json.dump(current_hashes, f, indent=2)
            Logger.log_info("Saved current workspace hashes to hashes.json")
        except Exception as e:
            Logger.log_warn(f"Failed to save hashes to hashes.json: {e}")

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