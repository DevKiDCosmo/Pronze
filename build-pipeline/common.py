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
qemu_display_mode = "serial"
qemu_pull_rate = 100
qemu_batch_buffer = ""
qemu_batch_lock = threading.Lock()
qemu_flusher_thread = None

# Build archiving and tracking state
build_number = 0
build_uuid = "N/A"

def get_stage_builds_file(workspace_dir):
    archive_dir = os.path.join(workspace_dir, ".archive")
    os.makedirs(archive_dir, exist_ok=True)
    return os.path.join(archive_dir, "stage_builds.json")

def load_stage_builds(workspace_dir):
    fpath = get_stage_builds_file(workspace_dir)
    if os.path.exists(fpath):
        try:
            with open(fpath, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_stage_builds(workspace_dir, data):
    fpath = get_stage_builds_file(workspace_dir)
    try:
        with open(fpath, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        Logger.log_warn(f"Failed to save stage builds: {e}")

def update_stage_build_info(workspace_dir, stage_name, status, compiled=True):
    data = load_stage_builds(workspace_dir)
    
    if stage_name not in data:
        data[stage_name] = {
            "build_number": 0,
            "uuid": "N/A",
            "status": "Pending",
            "timestamp": 0.0
        }
        
    if compiled:
        data[stage_name]["build_number"] += 1
        import uuid
        data[stage_name]["uuid"] = str(uuid.uuid4())
        
    data[stage_name]["status"] = status
    data[stage_name]["timestamp"] = time.time()
    
    save_stage_builds(workspace_dir, data)

def get_stats_file(workspace_dir):
    archive_dir = os.path.join(workspace_dir, ".archive")
    os.makedirs(archive_dir, exist_ok=True)
    return os.path.join(archive_dir, "stats.json")

def load_build_stats(workspace_dir):
    fpath = get_stats_file(workspace_dir)
    if os.path.exists(fpath):
        try:
            with open(fpath, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "total_builds": 0,
        "successful_builds": 0,
        "failed_builds": 0
    }

def save_build_stats(workspace_dir, stats):
    fpath = get_stats_file(workspace_dir)
    try:
        with open(fpath, 'w') as f:
            json.dump(stats, f, indent=2)
    except Exception as e:
        Logger.log_warn(f"Failed to save build stats: {e}")

def update_build_stats(workspace_dir, status):
    stats = load_build_stats(workspace_dir)
    stats["total_builds"] += 1
    if status == "Complete":
        stats["successful_builds"] += 1
    elif status == "Failed":
        stats["failed_builds"] += 1
    save_build_stats(workspace_dir, stats)

def init_build_info(workspace_dir):
    global build_number
    archive_dir = os.path.join(workspace_dir, ".archive")
    build_number_file = os.path.join(archive_dir, "build_number")
    if os.path.exists(build_number_file):
        try:
            with open(build_number_file, 'r') as f:
                build_number = int(f.read().strip())
        except Exception:
            pass

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

    kernel_cnt = get_count(kernel_match, os.path.join(context.src_dir, f"linux-{linux_ver}"), [os.path.join(context.download_dir, f"linux-{linux_ver}.tar.xz")], 0)
    busybox_cnt = get_count(busybox_match, os.path.join(context.src_dir, f"busybox-{busybox_ver}"), [os.path.join(context.download_dir, f"busybox-{busybox_ver}.tar.bz2")], 0)
    
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
            s6_cnt = 0

    mod_cnt = get_count(kernel_mod_match, os.path.join(context.workspace_dir, "kernel"), None, 0)
    
    sdk_cnt = 0
    if not sdk_match:
        sdk_cnt += count_compilable_files(os.path.join(context.workspace_dir, "sdk"))
        sdk_cnt += count_compilable_files(os.path.join(context.workspace_dir, "test"))
        
    daemon_cnt = get_count(daemon_match, os.path.join(context.workspace_dir, "daemon"), None, 0)

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
        "config_setup_dir": get_dir_hash(os.path.join(context.workspace_dir, "configuration_setup")),
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
            position: relative;
        }

        .stage-row {
            display: flex;
            justify-content: center;
            gap: 1.5rem;
            position: relative;
        }

        /* Building Facility Container */
        .building-facility-container {
            border: 3px solid var(--border-color);
            background-color: rgba(255, 255, 255, 0.4);
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.06'/%3E%3C/svg%3E");
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            padding: 1.25rem 1rem;
            width: fit-content;
            margin: 0.5rem auto;
            box-shadow: 6px 6px 0px var(--border-color);
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
            position: relative;
            z-index: 2;
        }

        .facility-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--border-color);
            padding-bottom: 0.5rem;
            margin-bottom: 0.25rem;
        }

        .facility-title {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
            font-weight: bold;
            letter-spacing: 0.05em;
            color: #212529;
        }

        .facility-timer {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
            font-weight: bold;
            color: #495057;
        }

        @keyframes dash {
            to {
                stroke-dashoffset: -20;
            }
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

        .node-meta {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.65rem;
            color: #495057;
            margin-top: 0.25rem;
            display: flex;
            justify-content: space-between;
            border-top: 1px dashed rgba(0, 0, 0, 0.1);
            padding-top: 0.25rem;
        }

        .stage-index {
            font-size: 0.75rem;
            font-weight: normal;
            color: #868e96;
            margin-left: 0.25rem;
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
        
        #qemu-settings-toggle-btn:hover {
            color: #ffffff !important;
            transform: rotate(30deg);
        }
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

    <!-- Error logs overlay modal -->
    <div class="modal-overlay" id="error-logs-modal">
        <div class="modal-box" style="width: 85%; max-width: 850px;">
            <div class="modal-title" id="error-logs-title">Stage Error Logs</div>
            <pre id="error-logs-content" style="background: #111; color: #f8f9fa; padding: 1rem; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; overflow-x: auto; max-height: 450px; white-space: pre-wrap; word-wrap: break-word; border: 3px solid var(--border-color); box-shadow: inset 0 0 10px rgba(0,0,0,0.5); text-align: left;"></pre>
            <div style="display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 1rem;">
                <button class="control-btn accent" onclick="hideErrorLogsModal()">Close</button>
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
                <div class="console-box-title">SOFTWARE & BUILD INFO</div>
                <div class="metric-row">
                    <span>BUILD NUMBER</span>
                    <span id="info-build-number">N/A</span>
                </div>
                <div class="metric-row">
                    <span>BUILD UUID</span>
                    <span id="info-build-uuid" style="font-size: 0.75rem; color: #adb5bd; word-break: break-all; text-align: right; max-width: 60%;">N/A</span>
                </div>
                <hr style="border: none; border-top: 1px solid var(--border-color); margin: 0.5rem 0;" />
                <div class="metric-row">
                    <span>TOTAL BUILDS</span>
                    <span id="info-total-builds">0</span>
                </div>
                <div class="metric-row">
                    <span>SUCCESSFUL BUILDS</span>
                    <span id="info-successful-builds" style="color: #37b24d; font-weight: bold;">0</span>
                </div>
                <div class="metric-row">
                    <span>FAILED BUILDS</span>
                    <span id="info-failed-builds" style="color: #f03e3e; font-weight: bold;">0</span>
                </div>
                <hr style="border: none; border-top: 1px solid var(--border-color); margin: 0.5rem 0;" />
                <div class="metric-row">
                    <span>KERNEL MODULE</span>
                    <span id="version-kernel-module">0.1</span>
                </div>
                <div class="metric-row">
                    <span>SDK VERSION</span>
                    <span id="version-sdk">0.1.0</span>
                </div>
                <div class="metric-row">
                    <span>FRAMEWORK</span>
                    <span style="font-style: italic; color: #ced4da;">Currently In Progress</span>
                </div>
                <div class="metric-row">
                    <span>DAEMON VERSION</span>
                    <span id="version-daemon">0.1.0</span>
                </div>
                <div class="metric-row">
                    <span>USERSPACE VERSION</span>
                    <span id="version-userspace">0.1.0</span>
                </div>
            </div>

            <div class="console-box">
                <div class="console-box-title">ADMIN CONTROLS</div>
                <div style="display: flex; flex-direction: column; gap: 0.5rem;">
                    <button class="control-btn" onclick="triggerUpdateBuild()">UPDATE BUILD</button>
                    <button class="control-btn" style="border-color: #2b8a3e; color: #2b8a3e; box-shadow: 2px 2px 0px #2b8a3e;" onclick="triggerRepackageBuild()">REPACKAGE ONLY</button>
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
                <div style="position: relative;" id="dag-container">
                    <svg id="dag-svg" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 3;"></svg>
                    <div class="dag-tree" style="position: relative; z-index: 2;">
                        <!-- Level 1 -->
                        <div class="stage-row">
                            <div class="node-card pending" id="node-DownloadTarballs" onclick="selectStage('DownloadTarballs')">
                                <div class="node-name">Download Tarballs</div>
                                <span class="node-status-label" id="status-DownloadTarballs">Pending</span>
                                <span class="node-duration" id="duration-DownloadTarballs"></span>
                                <div class="node-meta" id="meta-DownloadTarballs"></div>
                            </div>
                        </div>

                        <!-- Level 2 -->
                        <div class="stage-row">
                            <div class="node-card pending" id="node-CheckEarlyExit" onclick="selectStage('CheckEarlyExit')">
                                <div class="node-name">Verify Cache</div>
                                <span class="node-status-label" id="status-CheckEarlyExit">Pending</span>
                                <span class="node-duration" id="duration-CheckEarlyExit"></span>
                                <div class="node-meta" id="meta-CheckEarlyExit"></div>
                            </div>
                        </div>

                        <!-- Level 3 -->
                        <div class="stage-row">
                            <div class="node-card pending" id="node-ExtractTarballs" onclick="selectStage('ExtractTarballs')">
                                <div class="node-name">Extract Sources</div>
                                <span class="node-status-label" id="status-ExtractTarballs">Pending</span>
                                <span class="node-duration" id="duration-ExtractTarballs"></span>
                                <div class="node-meta" id="meta-ExtractTarballs"></div>
                            </div>
                        </div>

                        <!-- Building Facility -->
                        <div class="building-facility-container" id="building-facility-container">
                            <div class="facility-header">
                                <span class="facility-title">BUILDING FACILITY</span>
                                <span class="facility-timer" id="facility-timer">00:00</span>
                            </div>
                            <!-- Level 4 -->
                            <div class="stage-row">
                                <div class="node-card pending" id="node-CompileKernel" onclick="selectStage('CompileKernel')">
                                    <div class="node-name">Compile Kernel <span class="stage-index">[1/6]</span></div>
                                    <span class="node-status-label" id="status-CompileKernel">Pending</span>
                                    <span class="node-duration" id="duration-CompileKernel"></span>
                                    <div class="node-meta" id="meta-CompileKernel"></div>
                                </div>
                                <div class="node-card pending" id="node-CompileBusybox" onclick="selectStage('CompileBusybox')">
                                    <div class="node-name">Compile BusyBox <span class="stage-index">[2/6]</span></div>
                                    <span class="node-status-label" id="status-CompileBusybox">Pending</span>
                                    <span class="node-duration" id="duration-CompileBusybox"></span>
                                    <div class="node-meta" id="meta-CompileBusybox"></div>
                                </div>
                                <div class="node-card pending" id="node-CompileS6" onclick="selectStage('CompileS6')">
                                    <div class="node-name">Compile s6 <span class="stage-index">[3/6]</span></div>
                                    <span class="node-status-label" id="status-CompileS6">Pending</span>
                                    <span class="node-duration" id="duration-CompileS6"></span>
                                    <div class="node-meta" id="meta-CompileS6"></div>
                                </div>
                            </div>
                            <div class="stage-row">
                                <div class="node-card pending" id="node-CompileKernelModule" onclick="selectStage('CompileKernelModule')">
                                    <div class="node-name">Compile Driver <span class="stage-index">[4/6]</span></div>
                                    <span class="node-status-label" id="status-CompileKernelModule">Pending</span>
                                    <span class="node-duration" id="duration-CompileKernelModule"></span>
                                    <div class="node-meta" id="meta-CompileKernelModule"></div>
                                </div>
                                <div class="node-card pending" id="node-CompileSDK" onclick="selectStage('CompileSDK')">
                                    <div class="node-name">Compile SDK <span class="stage-index">[5/6]</span></div>
                                    <span class="node-status-label" id="status-CompileSDK">Pending</span>
                                    <span class="node-duration" id="duration-CompileSDK"></span>
                                    <div class="node-meta" id="meta-CompileSDK"></div>
                                </div>
                                <div class="node-card pending" id="node-CompileDaemon" onclick="selectStage('CompileDaemon')">
                                    <div class="node-name">Compile Daemon <span class="stage-index">[6/6]</span></div>
                                    <span class="node-status-label" id="status-CompileDaemon">Pending</span>
                                    <span class="node-duration" id="duration-CompileDaemon"></span>
                                    <div class="node-meta" id="meta-CompileDaemon"></div>
                                </div>
                            </div>
                        </div>

                        <!-- Imaging Bytes -->
                        <div class="building-facility-container" id="imaging-bytes-container">
                            <div class="facility-header">
                                <span class="facility-title">IMAGING BYTES</span>
                                <span class="facility-timer" id="imaging-bytes-timer">00:00</span>
                            </div>
                            <!-- Level 5 -->
                            <div class="stage-row">
                                <div class="node-card pending" id="node-AssembleRootfs" onclick="selectStage('AssembleRootfs')">
                                    <div class="node-name">Assemble RootFS <span class="stage-index">[1/5]</span></div>
                                    <span class="node-status-label" id="status-AssembleRootfs">Pending</span>
                                    <span class="node-duration" id="duration-AssembleRootfs"></span>
                                    <div class="node-meta" id="meta-AssembleRootfs"></div>
                                </div>
                            </div>

                            <!-- Level 5.5 -->
                            <div class="stage-row">
                                <div class="node-card pending" id="node-CopyConfigurationSetup" onclick="selectStage('CopyConfigurationSetup')">
                                    <div class="node-name">Copy Setup Files <span class="stage-index">[2/5]</span></div>
                                    <span class="node-status-label" id="status-CopyConfigurationSetup">Pending</span>
                                    <span class="node-duration" id="duration-CopyConfigurationSetup"></span>
                                    <div class="node-meta" id="meta-CopyConfigurationSetup"></div>
                                </div>
                            </div>

                            <!-- Level 6 -->
                            <div class="stage-row">
                                <div class="node-card pending" id="node-PackageBtrfsImage" onclick="selectStage('PackageBtrfsImage')">
                                    <div class="node-name">Btrfs Rootfs <span class="stage-index">[3/5]</span></div>
                                    <span class="node-status-label" id="status-PackageBtrfsImage">Pending</span>
                                    <span class="node-duration" id="duration-PackageBtrfsImage"></span>
                                    <div class="node-meta" id="meta-PackageBtrfsImage"></div>
                                </div>
                                <div class="node-card pending" id="node-PackageESPImage" onclick="selectStage('PackageESPImage')">
                                    <div class="node-name">ESP Boot <span class="stage-index">[4/5]</span></div>
                                    <span class="node-status-label" id="status-PackageESPImage">Pending</span>
                                    <span class="node-duration" id="duration-PackageESPImage"></span>
                                    <div class="node-meta" id="meta-PackageESPImage"></div>
                                </div>
                            </div>

                            <!-- Level 7 -->
                            <div class="stage-row">
                                <div class="node-card pending" id="node-AssembleGPTImage" onclick="selectStage('AssembleGPTImage')">
                                    <div class="node-name">Assemble GPT UEFI <span class="stage-index">[5/5]</span></div>
                                    <span class="node-status-label" id="status-AssembleGPTImage">Pending</span>
                                    <span class="node-duration" id="duration-AssembleGPTImage"></span>
                                    <div class="node-meta" id="meta-AssembleGPTImage"></div>
                                </div>
                            </div>
                        </div>

                        <!-- Level 8 -->
                        <div class="stage-row">
                            <div class="node-card pending" id="node-ShipImage" onclick="selectStage('ShipImage')">
                                <div class="node-name">Ship Image</div>
                                <span class="node-status-label" id="status-ShipImage">Pending</span>
                                <span class="node-duration" id="duration-ShipImage"></span>
                                <div class="node-meta" id="meta-ShipImage"></div>
                            </div>
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
                <div class="console-box-title" style="position: relative; display: flex; justify-content: space-between; align-items: center;">
                    <span>QEMU GUEST CONSOLE</span>
                    <div style="display: flex; gap: 0.5rem; align-items: center;">
                        <span id="qemu-status-text" style="color: #adb5bd;">STOPPED</span>
                        <button id="qemu-settings-toggle-btn" onclick="toggleQemuSettingsOverlay(event)" style="background: transparent; border: none; color: #ced4da; cursor: pointer; font-size: 1.1rem; padding: 0.2rem; display: flex; align-items: center; justify-content: center; outline: none; transition: color 0.2s, transform 0.2s;" title="QEMU Settings">⚙</button>
                    </div>
                    
                    <!-- Settings Overlay -->
                    <div id="qemu-settings-overlay" style="display: none; position: absolute; top: 100%; right: 10px; background: rgba(20, 20, 20, 0.95); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem 1rem; z-index: 100; box-shadow: 0 10px 25px rgba(0,0,0,0.5); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); flex-direction: column; gap: 0.75rem; width: 220px; font-family: inherit;">
                        <div style="display: flex; flex-direction: column; gap: 0.25rem;">
                            <label for="qemu-display-select" style="font-weight: 500; font-size: 0.75rem; color: #adb5bd; text-align: left; margin: 0;">Display Mode:</label>
                            <select id="qemu-display-select" onchange="updateQemuSettings()" style="background: rgba(255, 255, 255, 0.05); color: #fff; border: 1px solid var(--border-color); padding: 0.35rem 0.5rem; border-radius: 4px; font-family: inherit; font-size: 0.8rem; outline: none; cursor: pointer; width: 100%;">
                                <option value="graphics" style="background: #1e1e1e; color: #fff;">VGA Graphics (Render)</option>
                            </select>
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 0.25rem;">
                            <label for="qemu-pull-rate" style="font-weight: 500; font-size: 0.75rem; color: #adb5bd; text-align: left; margin: 0;">Log Pull Rate:</label>
                            <select id="qemu-pull-rate" onchange="updateQemuSettings()" style="background: rgba(255, 255, 255, 0.05); color: #fff; border: 1px solid var(--border-color); padding: 0.35rem 0.5rem; border-radius: 4px; font-family: inherit; font-size: 0.8rem; outline: none; cursor: pointer; width: 100%;">
                                <option value="50" style="background: #1e1e1e; color: #fff;">50 ms</option>
                                <option value="100" style="background: #1e1e1e; color: #fff;">100 ms</option>
                                <option value="250" style="background: #1e1e1e; color: #fff;">250 ms</option>
                                <option value="500" style="background: #1e1e1e; color: #fff;">500 ms</option>
                                <option value="1000" style="background: #1e1e1e; color: #fff;">1000 ms</option>
                            </select>
                        </div>
                    </div>
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

                <div id="qemu-keyboard-controls" style="display: none; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.5rem;">
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="copyQemuLogs()">Copy Logs</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="pasteToQemu()">Paste</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="sendQemuKey('\\t')">Tab</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="sendQemuKey('\\x1b[D')">←</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="sendQemuKey('\\x1b[A')">↑</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="sendQemuKey('\\x1b[B')">↓</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="sendQemuKey('\\x1b[C')">→</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="sendQemuKey('\\x1b[1;2D')">Shift+←</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="sendQemuKey('\\x1b[1;2A')">Shift+↑</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="sendQemuKey('\\x1b[1;2B')">Shift+↓</button>
                    <button class="control-btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="sendQemuKey('\\x1b[1;2C')">Shift+→</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let selectedStageName = "DownloadTarballs";
        let buildRunning = false;
        window.stageLogs = {};
        
        function selectStage(name) {
            selectedStageName = name;
            document.querySelectorAll(".node-card").forEach(el => el.classList.remove("selected"));
            const el = document.getElementById("node-" + name);
            if (el) el.classList.add("selected");
        }

        function showStageErrorLogs(name) {
            const logs = window.stageLogs ? window.stageLogs[name] : null;
            document.getElementById("error-logs-title").textContent = name + " Error Logs";
            const contentEl = document.getElementById("error-logs-content");
            if (logs) {
                contentEl.textContent = logs;
            } else {
                contentEl.textContent = "No log records found for " + name;
            }
            document.getElementById("error-logs-modal").classList.add("active");
        }

        function hideErrorLogsModal() {
            document.getElementById("error-logs-modal").classList.remove("active");
        }

        let timerInterval = null;
        let lastServerStartTime = 0;
        let lastServerCurrentTime = 0;
        let clientTimeAtPacket = 0;

        function startTimer(serverStartTime, serverCurrentTime) {
            lastServerStartTime = serverStartTime;
            lastServerCurrentTime = serverCurrentTime;
            clientTimeAtPacket = Date.now() / 1000;
            
            if (timerInterval) clearInterval(timerInterval);
            
            function tick() {
                if (lastServerStartTime > 0) {
                    const clientElapsed = (Date.now() / 1000) - clientTimeAtPacket;
                    const elapsed = (lastServerCurrentTime - lastServerStartTime) + clientElapsed;
                    updateTimerDisplay(elapsed);
                } else {
                    updateTimerDisplay(0);
                }
            }
            tick();
            timerInterval = setInterval(tick, 200);
        }

        function stopTimer(finalElapsed) {
            if (timerInterval) {
                clearInterval(timerInterval);
                timerInterval = null;
            }
            if (finalElapsed !== undefined && finalElapsed !== null) {
                updateTimerDisplay(finalElapsed);
            }
        }

        function updateTimerDisplay(elapsed) {
            if (elapsed === undefined || elapsed === null) elapsed = 0;
            let elapsedSec = Math.floor(elapsed);
            if (elapsedSec < 0) elapsedSec = 0;
            let mins = Math.floor(elapsedSec / 60).toString().padStart(2, '0');
            let secs = (elapsedSec % 60).toString().padStart(2, '0');
            document.getElementById("elapsed-time").textContent = `${mins}:${secs}`;
        }

        function toggleQemuSettingsOverlay(event) {
            event.stopPropagation();
            const overlay = document.getElementById("qemu-settings-overlay");
            if (overlay.style.display === "none" || overlay.style.display === "") {
                overlay.style.display = "flex";
            } else {
                overlay.style.display = "none";
            }
        }

        document.addEventListener("click", function(event) {
            const overlay = document.getElementById("qemu-settings-overlay");
            const toggleBtn = document.getElementById("qemu-settings-toggle-btn");
            if (overlay && overlay.style.display === "flex" && !overlay.contains(event.target) && event.target !== toggleBtn) {
                overlay.style.display = "none";
            }
        });

        // PasswordWall / Authentication Flow
        let pendingAction = null;
        let tempPassword = "";
        
        function getSavedPassword() {
            const savedTime = localStorage.getItem("portal_password_time");
            if (savedTime) {
                const elapsed = Date.now() - parseInt(savedTime, 10);
                if (elapsed > 10 * 60 * 1000) {
                    localStorage.removeItem("portal_password");
                    localStorage.removeItem("portal_password_time");
                    tempPassword = "";
                    return "";
                }
            }
            const pw = tempPassword || localStorage.getItem("portal_password") || "";
            if (pw) {
                localStorage.setItem("portal_password_time", Date.now().toString());
            }
            return pw;
        }
        
        function setSavedPassword(pw, remember) {
            localStorage.setItem("portal_password_time", Date.now().toString());
            if (remember) {
                localStorage.setItem("portal_password", pw);
                tempPassword = "";
            } else {
                localStorage.removeItem("portal_password");
                tempPassword = pw;
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
                    if (cb) {
                        cb(pw, (err) => {
                            if (err && err.status === 401) {
                                localStorage.removeItem("portal_password");
                                localStorage.removeItem("portal_password_time");
                                tempPassword = "";
                                showAuthModal(cb);
                            }
                        });
                    }
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
                        localStorage.removeItem("portal_password_time");
                        tempPassword = "";
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

        function triggerUpdateBuild() {
            performProtectedAction((pw, onDone) => {
                fetch("/api/build", {
                    method: "POST",
                    headers: { "X-Portal-Password": pw }
                })
                .then(res => {
                    if (res.status === 401) { onDone({status: 401}); return; }
                    if (res.ok) {
                        // Success: State will update dynamically via SSE
                    } else {
                        res.json().then(d => alert("Update build failed: " + (d.error || "unknown")));
                    }
                    onDone();
                })
                .catch(err => { alert("Update build failed: " + err); onDone(); });
            });
        }

        function triggerRepackageBuild() {
            performProtectedAction((pw, onDone) => {
                fetch("/api/repackage", {
                    method: "POST",
                    headers: { "X-Portal-Password": pw }
                })
                .then(res => {
                    if (res.status === 401) { onDone({status: 401}); return; }
                    if (res.ok) {
                        // Success: State will update dynamically via SSE
                    } else {
                        res.json().then(d => alert("Repackage failed: " + (d.error || "unknown")));
                    }
                    onDone();
                })
                .catch(err => { alert("Repackage failed: " + err); onDone(); });
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

        function updateQemuSettings() {
            performProtectedAction((pw, onDone) => {
                const display = document.getElementById("qemu-display-select").value;
                const pullRate = document.getElementById("qemu-pull-rate").value;
                fetch("/api/qemu/settings", {
                    method: "POST",
                    headers: { 
                        "Content-Type": "application/json",
                        "X-Portal-Password": pw
                    },
                    body: JSON.stringify({ display: display, pull_rate: parseInt(pullRate, 10) })
                })
                .then(res => {
                    if (res.status === 401) { onDone({status: 401}); return; }
                    onDone();
                })
                .catch(err => { console.error("Failed to update QEMU settings:", err); onDone(); });
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

        function sendQemuString(str) {
            performProtectedAction((pw, onDone) => {
                fetch("/api/qemu/input", {
                    method: "POST",
                    headers: { 
                        "X-Portal-Password": pw,
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({ input: str })
                })
                .then(res => {
                    if (res.status === 401) { onDone({status: 401}); return; }
                    onDone();
                })
                .catch(err => {
                    console.error("Error sending input: " + err);
                    onDone();
                });
            });
        }

        function sendQemuKey(keyStr) {
            sendQemuString(keyStr);
        }

        function sendQemuInput(e) {
            if (e.key === "Enter") {
                const inputField = document.getElementById("qemu-input-field");
                const val = inputField.value + "\\n";
                inputField.value = "";
                sendQemuString(val);
            }
        }

        function copyQemuLogs() {
            const term = document.getElementById("qemu-log-terminal");
            const text = term.innerText || term.textContent || "";
            navigator.clipboard.writeText(text).then(() => {
                alert("Logs copied to clipboard!");
            }).catch(err => {
                alert("Failed to copy logs: " + err);
            });
        }

        function pasteToQemu() {
            navigator.clipboard.readText().then(text => {
                if (text) {
                    sendQemuString(text);
                }
            }).catch(err => {
                const text = prompt("Paste text to QEMU guest console:");
                if (text) {
                    sendQemuString(text);
                }
            });
        }

        function ansiToHtml(text) {
            let escaped = text
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;");

            const ansiRegex = /\\x1b\\[([0-9;]*)m/g;
            let openSpans = 0;
            
            let html = escaped.replace(ansiRegex, (match, p1) => {
                if (!p1 || p1 === "0") {
                    let res = "";
                    while (openSpans > 0) {
                        res += "</span>";
                        openSpans--;
                    }
                    return res;
                }
                
                const codes = p1.split(";");
                let style = "";
                
                for (let i = 0; i < codes.length; i++) {
                    const code = parseInt(codes[i], 10);
                    if (isNaN(code)) continue;
                    
                    if (code === 1) {
                        style += "font-weight: bold;";
                    } else if (code === 3) {
                        style += "font-style: italic;";
                    } else if (code === 4) {
                        style += "text-decoration: underline;";
                    } else if (code >= 30 && code <= 37) {
                        const colors = [
                            "#2e3440", "#bf616a", "#a3be8c", "#ebcb8b",
                            "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0"
                        ];
                        style += "color: " + colors[code - 30] + ";";
                    } else if (code === 39) {
                        style += "color: inherit;";
                    } else if (code >= 40 && code <= 47) {
                        const bgColors = [
                            "#2e3440", "#bf616a", "#a3be8c", "#ebcb8b",
                            "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0"
                        ];
                        style += "background-color: " + bgColors[code - 40] + ";";
                    } else if (code === 49) {
                        style += "background-color: inherit;";
                    } else if (code >= 90 && code <= 97) {
                        const brightColors = [
                            "#4c566a", "#d08770", "#a3be8c", "#ebcb8b",
                            "#8fbcbb", "#b48ead", "#88c0d0", "#eceff4"
                        ];
                        style += "color: " + brightColors[code - 90] + ";";
                    } else if (code >= 100 && code <= 107) {
                        const brightBgColors = [
                            "#4c566a", "#d08770", "#a3be8c", "#ebcb8b",
                            "#8fbcbb", "#b48ead", "#88c0d0", "#eceff4"
                        ];
                        style += "background-color: " + brightBgColors[code - 100] + ";";
                    }
                }
                
                if (style) {
                    openSpans++;
                    return '<span style="' + style + '">';
                }
                return "";
            });
            
            while (openSpans > 0) {
                html += "</span>";
                openSpans--;
            }
            
            return html;
        }

        // QEMU Log viewer parser
        function parseLogLine(text) {
            let escaped = ansiToHtml(text);

            const timestampRegex = /^((?:<span[^>]*>)*)(\\s*\\[\\s*\\d+\\.\\d+\\s*\\])(.*)$/;
            const checkRegex = /^((?:<span[^>]*>)*)(\\s*\\[\\s*✔\\s*\\])(.*)$/;
            const crossRegex = /^((?:<span[^>]*>)*)(\\s*\\[\\s*x\\s*\\])(.*)$/;
            const warnRegex = /^((?:<span[^>]*>)*)(\\s*\\[\\s*!\\s*\\])(.*)$/;
            const infoRegex = /^((?:<span[^>]*>)*)(\\s*\\[\\s*i\\s*\\])(.*)$/;
            const stepRegex = /^((?:<span[^>]*>)*)(\\s*\\[\\s*(?:\\+|•)\\s*\\])(.*)$/;

            if (timestampRegex.test(escaped)) {
                return escaped.replace(timestampRegex, '$1<span class="term-tstamp">$2</span>$3');
            } else if (checkRegex.test(escaped)) {
                return escaped.replace(checkRegex, '$1<span class="term-check">$2</span><span class="term-check-text">$3</span>');
            } else if (crossRegex.test(escaped)) {
                return escaped.replace(crossRegex, '$1<span class="term-error">$2</span><span class="term-error-text">$3</span>');
            } else if (warnRegex.test(escaped)) {
                return escaped.replace(warnRegex, '$1<span class="term-warn">$2</span><span class="term-warn-text">$3</span>');
            } else if (infoRegex.test(escaped)) {
                return escaped.replace(infoRegex, '$1<span class="term-info">$2</span>$3');
            } else if (stepRegex.test(escaped)) {
                return escaped.replace(stepRegex, '$1<span class="term-step">$2</span>$3');
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
                if (line.includes("\\r")) {
                    const parts = line.split("\\r");
                    line = parts[parts.length - 1];
                }
                if (idx === lines.length - 1) {
                    html += parseLogLine(line);
                } else {
                    html += parseLogLine(line) + "\\n";
                }
            });
            term.innerHTML = html;
            term.scrollTop = term.scrollHeight;
        }

        // SVG Orthogonal connections drawing with rounded corners
        function getOrthogonalPath(x1, y1, x2, y2, R = 12) {
            if (Math.abs(x1 - x2) < 1) {
                return `M ${x1} ${y1} L ${x2} ${y2}`;
            }
            const r = Math.min(R, Math.abs(x2 - x1) / 2, Math.abs(y2 - y1) / 2);
            const ymid = y1 + (y2 - y1) / 2;
            const signX = x2 > x1 ? 1 : -1;
            return `M ${x1} ${y1} ` +
                   `L ${x1} ${ymid - r} ` +
                   `Q ${x1} ${ymid} ${x1 + r * signX} ${ymid} ` +
                   `L ${x2 - r * signX} ${ymid} ` +
                   `Q ${x2} ${ymid} ${x2} ${ymid + r} ` +
                   `L ${x2} ${y2}`;
        }

        function drawConnections() {
            const svg = document.getElementById("dag-svg");
            if (!svg) return;
            svg.innerHTML = "";
            
            const container = document.getElementById("dag-container");
            const containerRect = container.getBoundingClientRect();
            
            const connections = [
                { from: "DownloadTarballs", to: "CheckEarlyExit" },
                { from: "CheckEarlyExit", to: "ExtractTarballs" },
                { from: "ExtractTarballs", to: "CompileKernel" },
                { from: "ExtractTarballs", to: "CompileBusybox" },
                { from: "ExtractTarballs", to: "CompileS6" },
                { from: "CompileKernel", to: "CompileKernelModule" },
                { from: "CompileBusybox", to: "CompileSDK" },
                { from: "CompileS6", to: "CompileDaemon" },
                { from: "CompileKernelModule", to: "AssembleRootfs" },
                { from: "CompileSDK", to: "AssembleRootfs" },
                { from: "CompileDaemon", to: "AssembleRootfs" },
                { from: "AssembleRootfs", to: "CopyConfigurationSetup" },
                { from: "CopyConfigurationSetup", to: "PackageBtrfsImage" },
                { from: "CopyConfigurationSetup", to: "PackageESPImage" },
                { from: "PackageBtrfsImage", to: "AssembleGPTImage" },
                { from: "PackageESPImage", to: "AssembleGPTImage" },
                { from: "AssembleGPTImage", to: "ShipImage" }
            ];

            function getAnchorCoords(rect, anchor) {
                let x, y;
                if (anchor === "left") {
                    x = rect.left - containerRect.left;
                    y = rect.top + rect.height / 2 - containerRect.top;
                } else if (anchor === "right") {
                    x = rect.right - containerRect.left;
                    y = rect.top + rect.height / 2 - containerRect.top;
                } else if (anchor === "top") {
                    x = rect.left + rect.width / 2 - containerRect.left;
                    y = rect.top - containerRect.top;
                } else { // "bottom"
                    x = rect.left + rect.width / 2 - containerRect.left;
                    y = rect.bottom - containerRect.top;
                }
                return { x, y };
            }

            const pathGroups = {
                success: [],
                running: [],
                failed: [],
                pending: []
            };

            connections.forEach(conn => {
                const fromEl = document.getElementById("node-" + conn.from);
                const toEl = document.getElementById("node-" + conn.to);
                if (!fromEl || !toEl) return;
                
                const fromRect = fromEl.getBoundingClientRect();
                const toRect = toEl.getBoundingClientRect();
                
                const pt1 = getAnchorCoords(fromRect, "bottom");
                const pt2 = getAnchorCoords(toRect, "top");
                
                const x1 = pt1.x;
                const y1 = pt1.y;
                const x2 = pt2.x;
                const y2 = pt2.y;
                
                const d = getOrthogonalPath(x1, y1, x2, y2, 12);
                
                let group = "pending";
                if (fromEl.classList.contains("success") || fromEl.classList.contains("skipped")) {
                    group = "success";
                } else if (fromEl.classList.contains("running")) {
                    group = "running";
                } else if (fromEl.classList.contains("failed")) {
                    group = "failed";
                }
                pathGroups[group].push(d);
            });
            
            // Top connector line
            const firstCard = document.getElementById("node-DownloadTarballs");
            if (firstCard) {
                const firstRect = firstCard.getBoundingClientRect();
                const x1 = containerRect.width / 2;
                const y1 = 0;
                const x2 = (firstRect.left + firstRect.width / 2) - containerRect.left;
                const y2 = firstRect.top - containerRect.top;
                
                const d = `M ${x1} ${y1} L ${x2} ${y2}`;
                let group = "pending";
                if (firstCard.classList.contains("success") || firstCard.classList.contains("skipped")) {
                    group = "success";
                } else if (firstCard.classList.contains("running")) {
                    group = "running";
                }
                pathGroups[group].push(d);
            }
            
            // Bottom connector line
            const lastCard = document.getElementById("node-ShipImage");
            if (lastCard) {
                const lastRect = lastCard.getBoundingClientRect();
                const x1 = (lastRect.left + lastRect.width / 2) - containerRect.left;
                const y1 = lastRect.bottom - containerRect.top;
                const x2 = containerRect.width / 2;
                const y2 = containerRect.height;
                
                const d = `M ${x1} ${y1} L ${x2} ${y2}`;
                let group = "pending";
                if (lastCard.classList.contains("success") || lastCard.classList.contains("skipped")) {
                    group = "success";
                } else if (lastCard.classList.contains("running")) {
                    group = "running";
                }
                pathGroups[group].push(d);
            }

            // Render compound paths
            if (pathGroups.pending.length > 0) {
                const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                path.setAttribute("d", pathGroups.pending.join(" "));
                path.setAttribute("stroke", "var(--border-color)");
                path.setAttribute("stroke-width", "3");
                path.setAttribute("fill", "none");
                svg.appendChild(path);
            }
            if (pathGroups.success.length > 0) {
                const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                path.setAttribute("d", pathGroups.success.join(" "));
                path.setAttribute("stroke", "#37b24d");
                path.setAttribute("stroke-width", "3");
                path.setAttribute("fill", "none");
                svg.appendChild(path);
            }
            if (pathGroups.failed.length > 0) {
                const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                path.setAttribute("d", pathGroups.failed.join(" "));
                path.setAttribute("stroke", "#f03e3e");
                path.setAttribute("stroke-width", "3");
                path.setAttribute("fill", "none");
                svg.appendChild(path);
            }
            if (pathGroups.running.length > 0) {
                const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                path.setAttribute("d", pathGroups.running.join(" "));
                path.setAttribute("stroke", "#fcc419");
                path.setAttribute("stroke-width", "3");
                path.setAttribute("fill", "none");
                path.setAttribute("stroke-dasharray", "8, 6");
                path.style.animation = "dash 1s linear infinite";
                svg.appendChild(path);
            }
        }

        // Client-side Container duration calculations
        function calculateContainerDuration(stageNames, nodesState, serverCurrentTime) {
            if (!nodesState) return "00:00";
            let minStart = null;
            let maxEnd = null;
            let anyRunning = false;
            let anyStarted = false;

            stageNames.forEach(name => {
                const node = nodesState[name];
                if (node) {
                    if (node.status === "Running") {
                        anyRunning = true;
                    }
                    if (node.start_time !== null && node.start_time !== undefined) {
                        anyStarted = true;
                        if (minStart === null || node.start_time < minStart) {
                            minStart = node.start_time;
                        }
                    }
                    if (node.end_time !== null && node.end_time !== undefined) {
                        if (maxEnd === null || node.end_time > maxEnd) {
                            maxEnd = node.end_time;
                        }
                    }
                }
            });

            if (!anyStarted || minStart === null) {
                return "00:00";
            }

            let endTime = maxEnd;
            if (anyRunning || endTime === null) {
                endTime = serverCurrentTime || (Date.now() / 1000);
            }

            const elapsed = Math.max(0, endTime - minStart);
            const mins = Math.floor(elapsed / 60).toString().padStart(2, '0');
            const secs = Math.floor(elapsed % 60).toString().padStart(2, '0');
            return `${mins}:${secs}`;
        }

        function updateContainerTimers(serverCurrentTime) {
            const facilityStages = ['CompileKernel', 'CompileBusybox', 'CompileS6', 'CompileKernelModule', 'CompileSDK', 'CompileDaemon'];
            const imagingStages = ['AssembleRootfs', 'CopyConfigurationSetup', 'PackageBtrfsImage', 'PackageESPImage', 'AssembleGPTImage'];
            
            const facilityDuration = calculateContainerDuration(facilityStages, window.nodesState, serverCurrentTime);
            const imagingDuration = calculateContainerDuration(imagingStages, window.nodesState, serverCurrentTime);
            
            let facDone = 0;
            facilityStages.forEach(name => {
                const node = window.nodesState ? window.nodesState[name] : null;
                if (node && (node.status === "Success" || node.status === "Skipped" || (node.status === "Failed" && node.details && node.details.includes("[Allowed]")))) {
                    facDone++;
                }
            });
            
            let imgDone = 0;
            imagingStages.forEach(name => {
                const node = window.nodesState ? window.nodesState[name] : null;
                if (node && (node.status === "Success" || node.status === "Skipped" || (node.status === "Failed" && node.details && node.details.includes("[Allowed]")))) {
                    imgDone++;
                }
            });
            
            const facTimer = document.getElementById("facility-timer");
            if (facTimer) facTimer.textContent = `${facilityDuration} (${facDone}/6)`;
            
            const imgTimer = document.getElementById("imaging-bytes-timer");
            if (imgTimer) imgTimer.textContent = `${imagingDuration} (${imgDone}/5)`;
        }

        function updateNodeMeta(name, stageBuilds, versions) {
            const metaEl = document.getElementById("meta-" + name);
            if (!metaEl) return;
            
            const buildingStages = ["CompileKernel", "CompileBusybox", "CompileS6", "CompileKernelModule", "CompileSDK", "CompileDaemon"];
            if (!buildingStages.includes(name)) {
                metaEl.innerHTML = "";
                metaEl.style.display = "none";
                return;
            }
            metaEl.style.display = "";
            
            let buildCount = 0;
            let uuid = "N/A";
            if (stageBuilds && stageBuilds[name]) {
                buildCount = stageBuilds[name].build_number || 0;
                uuid = stageBuilds[name].uuid || "N/A";
                if (uuid && uuid !== "N/A") {
                    uuid = uuid.substring(0, 8);
                }
            }
            
            let ver = "";
            if (versions) {
                if (name === "CompileKernel") ver = versions.kernel;
                else if (name === "CompileBusybox") ver = versions.busybox;
                else if (name === "CompileS6") ver = versions.s6;
                else if (name === "CompileKernelModule") ver = versions.kernel_module;
                else if (name === "CompileSDK") ver = versions.sdk;
                else if (name === "CompileDaemon") ver = versions.daemon;
                else if (name === "AssembleRootfs") ver = versions.userspace;
            }
            
            let leftText = `#${buildCount}`;
            if (ver) {
                leftText = `v${ver} | #${buildCount}`;
            }
            metaEl.innerHTML = `<span class="meta-left">${leftText}</span><span class="meta-right">${uuid}</span>`;
        }

        function updateAllNodeMeta(stageBuilds, versions) {
            const stages = [
                "DownloadTarballs", "CheckEarlyExit", "ExtractTarballs",
                "CompileKernel", "CompileBusybox", "CompileS6",
                "CompileKernelModule", "CompileSDK", "CompileDaemon",
                "AssembleRootfs", "CopyConfigurationSetup",
                "PackageBtrfsImage", "PackageESPImage", "AssembleGPTImage", "ShipImage"
            ];
            stages.forEach(name => {
                updateNodeMeta(name, stageBuilds, versions);
            });
        }

        window.addEventListener("load", () => {
            setTimeout(drawConnections, 50);
        });
        window.addEventListener("resize", drawConnections);

        // SSE Connection
        const evtSource = new EventSource("/events");

        evtSource.addEventListener("state", (e) => {
            const payload = JSON.parse(e.data);
            const states = payload.nodes || payload;
            let activeCount = 0;
            let totalCount = Object.keys(states).length;
            let successCount = 0;
            
            if (payload.stage_builds) {
                window.stageBuilds = payload.stage_builds;
            }
            
            window.nodesState = states;
            
            for (const [name, data] of Object.entries(states)) {
                updateNode(name, data.status, data.details, data.elapsed);
                if (data.status === "Success" || data.status === "Skipped" || (data.status === "Failed" && data.details && data.details.includes("[Allowed]"))) {
                    successCount++;
                }
                if (data.status === "Running") {
                    activeCount++;
                    selectStage(name);
                }
            }
            
            updateContainerTimers(payload.server_current_time);
            updateAllNodeMeta(window.stageBuilds, payload.versions);
            
            if (payload.build_active) {
                startTimer(payload.build_start_time, payload.server_current_time);
                updateGlobalStatus("BUILDING", "building");
            } else if (payload.build_completed) {
                stopTimer(payload.elapsed_time);
                updateGlobalStatus(payload.final_status_text ? payload.final_status_text.toUpperCase() : "COMPLETE", payload.final_status_text && payload.final_status_text.toLowerCase() === "failed" ? "failed" : "success");
            } else {
                stopTimer(0);
                updateGlobalStatus("IDLE", "idle");
            }

            // Sync QEMU UI state
            if (payload.qemu_active) {
                document.getElementById("qemu-status-text").textContent = "RUNNING";
                document.getElementById("qemu-status-text").style.color = "#37b24d";
                document.getElementById("qemu-start-btn").disabled = true;
                document.getElementById("qemu-stop-btn").disabled = false;
                document.getElementById("qemu-input-container").style.display = "flex";
                document.getElementById("qemu-keyboard-controls").style.display = "flex";
            } else {
                document.getElementById("qemu-status-text").textContent = "STOPPED";
                document.getElementById("qemu-status-text").style.color = "#adb5bd";
                document.getElementById("qemu-start-btn").disabled = false;
                document.getElementById("qemu-stop-btn").disabled = true;
                document.getElementById("qemu-input-container").style.display = "none";
                document.getElementById("qemu-keyboard-controls").style.display = "none";
            }

            // Sync QEMU display and pull rate dropdown values
            if (payload.qemu_display_mode && document.activeElement !== document.getElementById("qemu-display-select")) {
                document.getElementById("qemu-display-select").value = payload.qemu_display_mode;
            }
            if (payload.qemu_pull_rate && document.activeElement !== document.getElementById("qemu-pull-rate")) {
                document.getElementById("qemu-pull-rate").value = payload.qemu_pull_rate;
            }

            // Sync Build Tracking and Software Versions info card
            if (payload.build_number) {
                document.getElementById("info-build-number").textContent = "#" + payload.build_number;
            } else {
                document.getElementById("info-build-number").textContent = "N/A";
            }
            if (payload.build_uuid) {
                document.getElementById("info-build-uuid").textContent = payload.build_uuid;
            } else {
                document.getElementById("info-build-uuid").textContent = "N/A";
            }
            
            // Sync Software Versions
            if (payload.versions) {
                document.getElementById("version-kernel-module").textContent = payload.versions.kernel_module;
                document.getElementById("version-sdk").textContent = payload.versions.sdk;
                document.getElementById("version-daemon").textContent = payload.versions.daemon;
                document.getElementById("version-userspace").textContent = payload.versions.userspace;
            }
            
            // Sync Build Stats
            if (payload.stats) {
                document.getElementById("info-total-builds").textContent = payload.stats.total_builds || 0;
                document.getElementById("info-successful-builds").textContent = payload.stats.successful_builds || 0;
                document.getElementById("info-failed-builds").textContent = payload.stats.failed_builds || 0;
            }

            // Mark skippable stages
            if (payload.allowed_failures) {
                payload.allowed_failures.forEach(name => {
                    const el = document.getElementById("node-" + name);
                    if (el && !el.querySelector(".optional-badge")) {
                        const badge = document.createElement("span");
                        badge.className = "optional-badge";
                        badge.textContent = " (optional)";
                        badge.style.fontSize = "0.7rem";
                        badge.style.color = "#868e96";
                        badge.style.fontStyle = "italic";
                        const nameEl = el.querySelector(".node-name");
                        if (nameEl) nameEl.appendChild(badge);
                    }
                });
            }
            
            updateBuildProgress(successCount, totalCount);
            setTimeout(drawConnections, 50);
        });
        
        evtSource.addEventListener("update", (e) => {
            const data = JSON.parse(e.data);
            if (data.stage_builds) {
                window.stageBuilds = data.stage_builds;
            }
            if (data.logs) {
                window.stageLogs[data.name] = data.logs;
            }
            
            if (!window.nodesState) window.nodesState = {};
            window.nodesState[data.name] = {
                status: data.status,
                details: data.details,
                elapsed: data.elapsed,
                start_time: data.start_time,
                end_time: data.end_time
            };
            
            updateNode(data.name, data.status, data.details, data.elapsed);
            updateContainerTimers(data.server_current_time);
            updateAllNodeMeta(window.stageBuilds, data.versions);
            
            if (data.status === "Running") {
                updateGlobalStatus("BUILDING", "building");
                selectStage(data.name);
                startTimer(data.build_start_time, data.server_current_time);
            }
            
            let cards = document.querySelectorAll(".node-card");
            let successCount = 0;
            cards.forEach(card => {
                if (card.classList.contains("success") || card.classList.contains("skipped") || (card.classList.contains("failed") && card.querySelector(".node-status-label")?.textContent.includes("Allowed"))) {
                    successCount++;
                }
            });
            updateBuildProgress(successCount, cards.length);
            setTimeout(drawConnections, 50);
        });

        evtSource.addEventListener("qemu_started", (e) => {
            document.getElementById("qemu-status-text").textContent = "RUNNING";
            document.getElementById("qemu-status-text").style.color = "#37b24d";
            document.getElementById("qemu-start-btn").disabled = true;
            document.getElementById("qemu-start-btn").textContent = "START QEMU";
            document.getElementById("qemu-stop-btn").disabled = false;
            document.getElementById("qemu-input-container").style.display = "flex";
            document.getElementById("qemu-keyboard-controls").style.display = "flex";
            document.getElementById("qemu-log-terminal").innerHTML = '<div style="color: #37b24d; font-weight: bold;">[+] Connecting to serial console...</div>';
            document.getElementById("qemu-input-field").focus();
            setTimeout(drawConnections, 50);
        });

        evtSource.addEventListener("qemu_stopped", (e) => {
            document.getElementById("qemu-status-text").textContent = "STOPPED";
            document.getElementById("qemu-status-text").style.color = "#adb5bd";
            document.getElementById("qemu-start-btn").disabled = false;
            document.getElementById("qemu-stop-btn").disabled = true;
            document.getElementById("qemu-input-container").style.display = "none";
            document.getElementById("qemu-keyboard-controls").style.display = "none";
            setTimeout(drawConnections, 50);
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
 
        evtSource.addEventListener("initial_logs", (e) => {
            const logs = JSON.parse(e.data);
            if (logs) {
                for (const [name, logText] of Object.entries(logs)) {
                    window.stageLogs[name] = logText;
                }
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
            
            let inspectBtn = el.querySelector(".error-inspect-btn");
            if (status === "Failed") {
                if (!inspectBtn) {
                    inspectBtn = document.createElement("button");
                    inspectBtn.className = "error-inspect-btn";
                    inspectBtn.innerHTML = "ℹ";
                    inspectBtn.title = "View Compilation Errors";
                    inspectBtn.style.position = "absolute";
                    inspectBtn.style.top = "5px";
                    inspectBtn.style.right = "5px";
                    inspectBtn.style.background = "#f03e3e";
                    inspectBtn.style.color = "#ffffff";
                    inspectBtn.style.border = "none";
                    inspectBtn.style.borderRadius = "50%";
                    inspectBtn.style.width = "18px";
                    inspectBtn.style.height = "18px";
                    inspectBtn.style.fontSize = "0.7rem";
                    inspectBtn.style.display = "flex";
                    inspectBtn.style.alignItems = "center";
                    inspectBtn.style.justifyContent = "center";
                    inspectBtn.style.cursor = "pointer";
                    inspectBtn.style.zIndex = "10";
                    inspectBtn.style.outline = "none";
                    inspectBtn.onclick = function(e) {
                        e.stopPropagation();
                        showStageErrorLogs(name);
                    };
                    el.style.position = "relative";
                    el.appendChild(inspectBtn);
                } else {
                    inspectBtn.style.display = "flex";
                }
            } else {
                if (inspectBtn) {
                    inspectBtn.style.display = "none";
                }
            }
            
            const statusEl = document.getElementById("status-" + name);
            if (statusEl) {
                if (status === "Success") {
                    statusEl.textContent = "Complete";
                } else if (status === "Failed" && details && details.includes("[Allowed]")) {
                    statusEl.textContent = "Failed (Allowed)";
                } else {
                    statusEl.textContent = status;
                }
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
    
    state = node_states.get(name, {"status": "Pending", "details": "Waiting in queue...", "elapsed": None, "start_time": None, "end_time": None})
    state["status"] = status
    state["details"] = details
    state["elapsed"] = elapsed
    
    if status == "Running":
        state["start_time"] = time.time()
        state["end_time"] = None
    elif status in ("Success", "Failed"):
        if state.get("start_time") is None:
            if elapsed is not None:
                state["start_time"] = time.time() - elapsed
            else:
                state["start_time"] = time.time()
        if elapsed is not None:
            state["end_time"] = state["start_time"] + elapsed
        else:
            state["end_time"] = time.time()
    elif status == "Skipped":
        state["start_time"] = None
        state["end_time"] = None
        
    node_states[name] = state
    
    ws_dir = global_context.workspace_dir if global_context else "."
    if status in ("Success", "Failed"):
        update_stage_build_info(ws_dir, name, status, compiled=True)
    elif status == "Skipped":
        update_stage_build_info(ws_dir, name, "Skipped", compiled=False)
    else:
        update_stage_build_info(ws_dir, name, status, compiled=False)
        
    stage_builds = load_stage_builds(ws_dir)
    
    # If no_view is enabled, do not attempt to stream events
    if no_view_flag:
        return
        
    payload = {
        "name": name,
        "status": status,
        "details": details,
        "elapsed": elapsed,
        "start_time": state["start_time"],
        "end_time": state["end_time"],
        "logs": logs,
        "build_start_time": build_start_time,
        "server_current_time": time.time(),
        "stage_builds": stage_builds,
        "versions": get_state_payload()["versions"]
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
            
            state_payload = get_state_payload()
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
                Logger.log_info("Rebuild pipeline requested from web view - clearing cache flags to force compilation")
                # Clear all .done and .hash files in output-nochanges to force rebuild
                if os.path.isdir(global_context.nochanges_dir):
                    for f in os.listdir(global_context.nochanges_dir):
                        if f.endswith(".done") or f.endswith(".hash") or f == "hashes.json":
                            try:
                                os.remove(os.path.join(global_context.nochanges_dir, f))
                            except Exception as e:
                                Logger.log_warn(f"Failed to clear cache file {f} during rebuild: {e}")
                # Trigger rebuild
                build_queue.put((global_context, global_pipeline))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            else:
                self.send_error(500, "Context/Pipeline not initialized")
                
        elif self.path == '/api/build':
            if global_context and global_pipeline:
                if build_active:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Build already active"}).encode('utf-8'))
                    return
                Logger.log_info("Incremental/Update build requested from web view")
                # Trigger build without clearing cache
                build_queue.put((global_context, global_pipeline))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            else:
                self.send_error(500, "Context/Pipeline not initialized")
                
        elif self.path == '/api/repackage':
            if global_context and global_pipeline:
                if build_active:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Build already active"}).encode('utf-8'))
                    return
                Logger.log_info("Repackage only requested from web view")
                # Clear ONLY the master.done/master.hash flags to bypass CheckEarlyExit
                master_done = os.path.join(global_context.nochanges_dir, "master.done")
                master_hash = os.path.join(global_context.nochanges_dir, "master.hash")
                if os.path.exists(master_done):
                    try:
                        os.remove(master_done)
                    except Exception:
                        pass
                if os.path.exists(master_hash):
                    try:
                        os.remove(master_hash)
                    except Exception:
                        pass
                # Also reset CheckEarlyExit state done flag
                cee_done = os.path.join(global_context.nochanges_dir, "CheckEarlyExit.done")
                if os.path.exists(cee_done):
                    try:
                        os.remove(cee_done)
                    except Exception:
                        pass
                global_context.repackage_only_active = True
                build_queue.put((global_context, global_pipeline))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            else:
                self.send_error(500, "Context/Pipeline not initialized")
                
        elif self.path == '/api/qemu/settings':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                global qemu_display_mode, qemu_pull_rate
                qemu_display_mode = data.get("display", qemu_display_mode)
                qemu_pull_rate = data.get("pull_rate", qemu_pull_rate)
                
                if global_context:
                    broadcast_status(global_context)
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
                return
            except Exception as e:
                Logger.log_error(f"Error updating QEMU settings: {e}")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid settings input"}).encode('utf-8'))
                
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

def get_state_payload():
    elapsed_time = 0.0
    if build_active and build_start_time > 0:
        elapsed_time = time.time() - build_start_time
    elif build_completed:
        elapsed_time = total_build_time
        
    ws_dir = global_context.workspace_dir if global_context else "."
    stats = load_build_stats(ws_dir)
    stage_builds = load_stage_builds(ws_dir)
    
    # Extract version details from config
    versions = {}
    allowed_failures = []
    if global_context and hasattr(global_context, 'config'):
        versions = {
            "kernel": global_context.config.get("LINUX_VERSION", "N/A"),
            "busybox": global_context.config.get("BUSYBOX_VERSION", "N/A"),
            "s6": global_context.config.get("S6_VERSION", "N/A"),
            "kernel_module": global_context.config.get("KERNEL_MODULE_VERSION", "0.1"),
            "sdk": global_context.config.get("SDK_VERSION", "0.1.0"),
            "daemon": global_context.config.get("DAEMON_VERSION", "0.1.0"),
            "userspace": global_context.config.get("USERSPACE_VERSION", "0.1.0")
        }
        allowed_failures_raw = global_context.config.get("ALLOWED_FAILURES", "")
        allowed_failures = [x.strip() for x in allowed_failures_raw.split(",") if x.strip()]
    else:
        versions = {
            "kernel": "N/A",
            "busybox": "N/A",
            "s6": "N/A",
            "kernel_module": "0.1",
            "sdk": "0.1.0",
            "daemon": "0.1.0",
            "userspace": "0.1.0"
        }
        
    return {
        "nodes": node_states,
        "build_start_time": build_start_time,
        "server_current_time": time.time(),
        "build_completed": build_completed,
        "build_active": build_active,
        "qemu_active": qemu_process is not None and qemu_process.poll() is None,
        "qemu_display_mode": qemu_display_mode,
        "qemu_pull_rate": qemu_pull_rate,
        "elapsed_time": elapsed_time,
        "build_number": build_number,
        "build_uuid": build_uuid,
        "stats": stats,
        "stage_builds": stage_builds,
        "versions": versions,
        "allowed_failures": allowed_failures
    }

def broadcast_status(context):
    if no_view_flag:
        return
    
    state_payload = get_state_payload()
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
        "/opt/homebrew/share/qemu/edk2-x86_64-code.fd",
        "/opt/homebrew/share/qemu/OVMF.fd",
        "/usr/share/OVMF/OVMF_CODE.fd",
        "/usr/share/ovmf/OVMF.fd",
        "/usr/share/qemu/OVMF.fd",
        "/opt/local/share/qemu/edk2-x86_64-code.fd",
        "/usr/share/ovmf/x64/OVMF_CODE.fd",
    ]
    for p in paths:
        if os.path.isfile(p):
            return p
    # Search fallback
    for root_dir in ["/opt/homebrew/share/qemu", "/usr/share/OVMF", "/usr/share/ovmf", "/usr/share/qemu"]:
        if os.path.isdir(root_dir):
            for root, dirs, files in os.walk(root_dir):
                for f in files:
                    if f in ["edk2-x86_64-code.fd", "OVMF.fd", "OVMF_CODE.fd"]:
                        return os.path.join(root, f)
    return None

def flush_qemu_logs():
    global qemu_batch_buffer
    to_send = ""
    with qemu_batch_lock:
        if qemu_batch_buffer:
            to_send = qemu_batch_buffer
            qemu_batch_buffer = ""
    if to_send:
        msg = f"event: qemu_log\ndata: {json.dumps(to_send)}\n\n"
        for q in list(clients):
            try:
                q.put(msg)
            except Exception:
                pass

def qemu_flusher():
    global qemu_process, qemu_pull_rate
    while True:
        proc = qemu_process
        if not proc or proc.poll() is not None:
            flush_qemu_logs()
            break
        flush_qemu_logs()
        time.sleep(qemu_pull_rate / 1000.0)

def qemu_reader():
    global qemu_process, qemu_log_buffer, qemu_batch_buffer
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
            with qemu_batch_lock:
                qemu_batch_buffer += char
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
    global qemu_process, qemu_log_buffer, qemu_reader_thread, qemu_flusher_thread, qemu_display_mode
    if qemu_process and qemu_process.poll() is None:
        return True, "QEMU is already running."
    
    img_path = os.path.join(workspace_dir, "output", "pronzeos.img")
    if not os.path.exists(img_path):
        return False, f"Disk image not found at {img_path}. Please build first!"
        
    ovmf_path = find_ovmf_firmware()
    
    # Detect Host Platform and select proper hypervisor acceleration
    accel_args = []
    import platform
    os_type = platform.system()
    if os_type == "Darwin":
        import subprocess as sp
        try:
            arch = sp.check_output(["uname", "-m"]).decode("utf-8").strip()
        except Exception:
            arch = "x86_64"
        if arch == "x86_64":
            accel_args = ["-accel", "hvf", "-cpu", "Penryn"]
        else:
            accel_args = ["-cpu", "max"]
    elif os_type == "Linux":
        if os.access("/dev/kvm", os.R_OK | os.W_OK):
            accel_args = ["-accel", "kvm", "-cpu", "host"]
        else:
            accel_args = []

    qemu_cmd = ["qemu-system-x86_64"]
    qemu_cmd.extend(accel_args)
    
    if ovmf_path:
        qemu_cmd.extend(["-drive", f"if=pflash,format=raw,unit=0,file={ovmf_path},readonly=on"])
        Logger.log_info(f"Using UEFI firmware via pflash: {ovmf_path}")
    else:
        Logger.log_warn("No UEFI firmware found. systemd-boot inside guest might fail.")
        
    qemu_cmd.extend(["-m", "1G", "-hda", img_path])
    
    if qemu_display_mode == "serial":
        qemu_cmd.extend(["-display", "none", "-serial", "stdio"])
    else:
        # Graphics display mode (headless but VGA emulator enabled for guest OS)
        qemu_cmd.extend(["-display", "none", "-vga", "std", "-serial", "stdio"])
        Logger.log_info("Using headless display backend for VGA graphics (standard emulation)")

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
        
        qemu_flusher_thread = threading.Thread(target=qemu_flusher, daemon=True)
        qemu_flusher_thread.start()
        
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
    global build_active, build_completed, total_build_time, final_status_text, build_start_time, build_uuid, global_pipeline
    while True:
        task = build_queue.get()
        if task is None:
            build_queue.task_done()
            break
        
        context, pipeline = task
        global_pipeline = pipeline
        build_active = True
        try:
            # Re-create all context directories to prevent errors if they were deleted by fclean/clean
            os.makedirs(context.opt_dir, exist_ok=True)
            os.makedirs(context.download_dir, exist_ok=True)
            os.makedirs(context.src_dir, exist_ok=True)
            os.makedirs(context.output_dir, exist_ok=True)
            os.makedirs(context.work_dir, exist_ok=True)
            os.makedirs(context.rootfs_dir, exist_ok=True)
            os.makedirs(context.nochanges_dir, exist_ok=True)

            # Generate new UUID for the active build
            import uuid
            build_uuid = str(uuid.uuid4())

            # Reset build state
            build_completed = False
            total_build_time = 0.0
            final_status_text = ""
            build_start_time = time.time()
            
            # Reset node states in the UI
            for node in pipeline.execution_order:
                node_states[node.name] = {"status": "Pending", "details": "Waiting in queue...", "elapsed": None, "start_time": None, "end_time": None}
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

def status_broadcast_loop():
    global build_active, global_context
    while True:
        if build_active and global_context:
            try:
                broadcast_status(global_context)
            except Exception:
                pass
        time.sleep(1)

# Start background status broadcast loop thread
threading.Thread(target=status_broadcast_loop, daemon=True).start()

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
        init_build_info(self.workspace_dir)

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

def archive_modules_separately(context):
    try:
        archive_dir = os.path.join(context.workspace_dir, ".archive")
        os.makedirs(archive_dir, exist_ok=True)
        
        build_number_file = os.path.join(archive_dir, "build_number")
        build_num = 1
        if os.path.exists(build_number_file):
            try:
                with open(build_number_file, 'r') as f:
                    build_num = int(f.read().strip())
            except Exception:
                pass

        import datetime
        
        modules_config = [
            {
                "name": "kernel",
                "version": context.config.get("LINUX_VERSION", "6.6.21"),
                "stage": "CompileKernel",
                "files": [("bzImage", "bzImage")]
            },
            {
                "name": "busybox",
                "version": context.config.get("BUSYBOX_VERSION", "1.36.1"),
                "stage": "CompileBusybox",
                "files": [("busybox_install.tar.gz", "busybox_install.tar.gz")]
            },
            {
                "name": "s6",
                "version": context.config.get("S6_VERSION", "2.11.3.2"),
                "stage": "CompileS6",
                "files": [("s6_install.tar.gz", "s6_install.tar.gz")]
            },
            {
                "name": "kernel_module",
                "version": context.config.get("KERNEL_MODULE_VERSION", "0.1"),
                "stage": "CompileKernelModule",
                "files": [("pronze.ko", "pronze.ko")]
            },
            {
                "name": "sdk",
                "version": context.config.get("SDK_VERSION", "0.1.0"),
                "stage": "CompileSDK",
                "files": [
                    ("libpronze.so", "libpronze.so"),
                    ("test_alloc", "test_alloc"),
                    ("test_bounds", "test_bounds"),
                    ("test_zig", "test_zig"),
                    ("test_rust", "test_rust")
                ]
            },
            {
                "name": "daemon",
                "version": context.config.get("DAEMON_VERSION", "0.1.0"),
                "stage": "CompileDaemon",
                "files": [("pronzed", "pronzed")]
            },
            {
                "name": "userspace",
                "version": context.config.get("USERSPACE_VERSION", "0.1.0"),
                "stage": "AssembleGPTImage",
                "files": [("pronzeos.img", "pronzeos.img")]
            }
        ]

        stage_builds = load_stage_builds(context.workspace_dir)

        for mod in modules_config:
            copied_files = []
            mod_dest_dir = os.path.join(archive_dir, mod["name"], mod["version"])
            
            for src_name, dest_name in mod["files"]:
                src_path = os.path.join(context.output_dir, src_name)
                if not os.path.exists(src_path):
                    src_path = os.path.join(context.nochanges_dir, src_name)
                
                if not os.path.exists(src_path):
                    if src_name == "pronze.ko":
                        src_path = os.path.join(context.workspace_dir, "kernel/pronze.ko")
                    elif src_name == "libpronze.so":
                        src_path = os.path.join(context.workspace_dir, "sdk/c/src/libpronze.so")
                    elif src_name == "pronzed":
                        src_path = os.path.join(context.workspace_dir, "daemon/target/x86_64-unknown-linux-musl/release/pronzed")
                    elif src_name.startswith("test_"):
                        if src_name == "test_rust":
                            src_path = os.path.join(context.workspace_dir, "test/test_rust/target/x86_64-unknown-linux-musl/release/test_rust")
                        else:
                            src_path = os.path.join(context.workspace_dir, "test", src_name)
                
                if os.path.exists(src_path):
                    os.makedirs(mod_dest_dir, exist_ok=True)
                    shutil.copy2(src_path, os.path.join(mod_dest_dir, dest_name))
                    copied_files.append(dest_name)
            
            if copied_files:
                stage_info = stage_builds.get(mod["stage"], {})
                mod_uuid = stage_info.get("uuid", "N/A")
                mod_build_num = stage_info.get("build_number", build_num)
                
                meta_path = os.path.join(mod_dest_dir, "meta.json")
                meta_data = {
                    "uuid": mod_uuid,
                    "build_number": mod_build_num,
                    "module": mod["name"],
                    "version": mod["version"],
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "files": copied_files,
                    "versions": {
                        "kernel_module": context.config.get("KERNEL_MODULE_VERSION", "0.1"),
                        "sdk": context.config.get("SDK_VERSION", "0.1.0"),
                        "framework": "Currently In Progress",
                        "daemon": context.config.get("DAEMON_VERSION", "0.1.0"),
                        "userspace": context.config.get("USERSPACE_VERSION", "0.1.0")
                    }
                }
                with open(meta_path, 'w') as f:
                    json.dump(meta_data, f, indent=2)
                Logger.log_success(f"Archived module '{mod['name']}' v{mod['version']} to {mod_dest_dir}")
    except Exception as e:
        Logger.log_warn(f"Failed to archive individual modules: {e}")

class PipelineNode:
    def __init__(self, name, dependencies=None):
        self.name = name
        self.dependencies = dependencies or []
        node_states[name] = {"status": "Pending", "details": "Waiting in queue...", "elapsed": None, "start_time": None, "end_time": None}

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
        
        repackage_mode = getattr(context, "repackage_only_active", False)
        building_stages = ["CompileKernel", "CompileBusybox", "CompileS6", "CompileKernelModule", "CompileSDK", "CompileDaemon"]

        for node in self.execution_order:
            if repackage_mode and node.name in building_stages:
                update_node_status(node.name, "Skipped", "Skipped (Repackage Only)")
                continue

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
                allowed_failures_raw = context.config.get("ALLOWED_FAILURES", "")
                allowed_failures = [x.strip() for x in allowed_failures_raw.split(",") if x.strip()]
                if node.name in allowed_failures:
                    update_node_status(node.name, "Failed", f"Error ({elapsed_t:.2f}s) [Allowed]", elapsed=elapsed_t)
                    Logger.log_warn(f"Stage {node.name} failed, but failure is allowed under ALLOWED_FAILURES. Continuing pipeline.")
                    continue
                else:
                    update_node_status(node.name, "Failed", f"Error ({elapsed_t:.2f}s)", elapsed=elapsed_t)
                    Logger.log_error(f"Failed executing {node.name}: {e}")
                    
                    total_build_time = time.time() - overall_start_t
                    final_status_text = "Failed"
                    build_completed = True
                    send_total_report(total_build_time, "Failed")
                    update_build_stats(context.workspace_dir, "Failed")
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
 
        global build_number, build_uuid
        # Increment build number and archive if not skipped
        if not getattr(context, 'skip_remaining', False):
            update_build_stats(context.workspace_dir, "Complete")
            archive_modules_separately(context)
            try:
                archive_dir = os.path.join(context.workspace_dir, ".archive")
                os.makedirs(archive_dir, exist_ok=True)
                build_number_file = os.path.join(archive_dir, "build_number")
                build_number = 1
                if os.path.exists(build_number_file):
                    try:
                        with open(build_number_file, 'r') as f:
                            build_number = int(f.read().strip()) + 1
                    except Exception:
                        pass
                with open(build_number_file, 'w') as f:
                    f.write(str(build_number))
                
                # Use the UUID generated at the start of build in build_worker
                # Archive outputs
                target = context.target  # "distro" or "module"
                version = "0.1.0" if target == "distro" else "0.1"
                
                dest_dir = os.path.join(archive_dir, target, version)
                os.makedirs(dest_dir, exist_ok=True)
                
                copied_files = []
                if target == "distro":
                    img_src = os.path.join(context.output_dir, "pronzeos.img")
                    if os.path.exists(img_src):
                        shutil.copy2(img_src, os.path.join(dest_dir, "pronzeos.img"))
                        copied_files.append("pronzeos.img")
                elif target == "module":
                    ko_src = os.path.join(context.output_dir, "pronze.ko")
                    if os.path.exists(ko_src):
                        shutil.copy2(ko_src, os.path.join(dest_dir, "pronze.ko"))
                        copied_files.append("pronze.ko")
                
                # Write meta.json
                import datetime
                meta_path = os.path.join(dest_dir, "meta.json")
                meta_data = {
                    "uuid": build_uuid,
                    "build_number": build_number,
                    "target": target,
                    "version": version,
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "files": copied_files,
                    "versions": {
                        "kernel_module": "0.1",
                        "sdk": "0.1.0",
                        "framework": "Currently In Progress",
                        "daemon": "0.1.0",
                        "userspace": "0.1.0"
                    }
                }
                with open(meta_path, 'w') as f:
                    json.dump(meta_data, f, indent=2)
                Logger.log_success(f"Archived build #{build_number} (UUID: {build_uuid}) to {dest_dir}")
            except Exception as e:
                Logger.log_warn(f"Failed to archive build: {e}")
        
        if hasattr(context, "repackage_only_active"):
            context.repackage_only_active = False

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