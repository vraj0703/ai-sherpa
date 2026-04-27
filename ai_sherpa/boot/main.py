#!/usr/bin/env python3
"""
Boot Sherpa -- Raj Sadan System Initializer
Authority: CONSTITUTION.toml, Article IV + Article XV
Protocol: PROTOCOL-03, PROTOCOL-11 (Life Cycle Framework)

PROTOCOL-11 Boot Sequence:
  Step 0: Load credentials (~1s)
  Step 1: Start Ollama (blocking, 3-15s)
  Step 2: Detect boot mode from state/session.toml (<1s)
  Step 3: Start 5 services in PARALLEL via ThreadPoolExecutor (15-20s max)
          Dashboard(3482) | Brain(3483) | Knowledge(3484) | WhatsApp(3478) | Cron(3479)
  Step 4: Infrastructure checks in PARALLEL (overlapping with Step 3)
          Pi SSH | Tailscale | PostgreSQL
  Step 5: Start Cortex LAST + handover (5-10s)
  Step 6: Scrum board evaluation (~2s)
  Step 7: Build boot prompt from session.toml (clean/crash) or LLM memory (cold-start)
  Step 8: Launch Claude Code
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ─── Constants ───

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MEMORY_DIR = PROJECT_ROOT / "memory" / "journal"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.toml"
BOOT_LOCKFILE = PROJECT_ROOT / ".claude" / "boot.lock"
SESSION_FILE = PROJECT_ROOT / "senses" / "interoception" / "state" / "session.toml"
DECISIONS_LOG = PROJECT_ROOT / "senses" / "nociception" / "logs" / "cortex-decisions.jsonl"


# ─── Lockfile (prevents concurrent boots / re-entry loops) ───


def _acquire_lock():
    """Create a PID lockfile. Returns True if lock acquired, False if another boot is running."""
    BOOT_LOCKFILE.parent.mkdir(parents=True, exist_ok=True)

    if BOOT_LOCKFILE.exists():
        try:
            old_pid = int(BOOT_LOCKFILE.read_text().strip())
            # Check if that PID is still alive
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {old_pid}", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                if str(old_pid) in result.stdout:
                    return False  # Still running
            else:
                os.kill(old_pid, 0)  # Signal 0 = check existence
                return False  # Still running
        except (ValueError, OSError, subprocess.TimeoutExpired, PermissionError):
            pass  # Stale lock -- process is dead, safe to overwrite

    BOOT_LOCKFILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock():
    """Remove the lockfile on clean exit."""
    try:
        if BOOT_LOCKFILE.exists():
            current_pid = BOOT_LOCKFILE.read_text().strip()
            if current_pid == str(os.getpid()):
                BOOT_LOCKFILE.unlink()
    except OSError:
        pass


def _is_inside_claude_session():
    """Detect if we're already inside a Claude Code session.
    Checks multiple signals since CLAUDECODE env var is unreliable."""
    # Check env vars that Claude Code sets
    if os.environ.get("CLAUDECODE"):
        return True
    if os.environ.get("CLAUDE_CODE"):
        return True
    # Check if parent process is claude (walk up the process tree)
    if platform.system() == "Windows":
        try:
            # Check if any ancestor process is claude.exe
            ppid = os.getppid()
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={ppid}",
                 "get", "Name", "/value"],
                capture_output=True, text=True, timeout=5,
            )
            if "claude" in result.stdout.lower():
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass
    else:
        try:
            ppid = os.getppid()
            cmdline = Path(f"/proc/{ppid}/cmdline").read_text()
            if "claude" in cmdline.lower():
                return True
        except (OSError, FileNotFoundError):
            pass
    return False


def _normalize_ollama_host(host):
    """Normalize OLLAMA_HOST -- add http:// if missing, default to localhost."""
    if not host:
        return "http://localhost:11434"
    if host.startswith("http://") or host.startswith("https://"):
        return host
    # Bare host like "0.0.0.0:11434" -- add protocol and remap 0.0.0.0 to localhost
    host_part = host.split(":")[0]
    if host_part == "0.0.0.0":
        host = host.replace("0.0.0.0", "localhost", 1)
    return f"http://{host}"


OLLAMA_HOST = _normalize_ollama_host(os.environ.get("OLLAMA_HOST", ""))

CONSTITUTIONAL_FILES = [
    "AGENT.toml",
    "CLAUDE.md",
    "CONSTITUTION.toml",
    "IDENTITY.toml",
    "USER.toml",
]

# Boot Sherpa uses the lightest model for its own reasoning tasks
BOOT_MODEL = "llama3.2:3b"
BOOT_MODEL_FALLBACK = "phi3:latest"

# ─── Service Constants ───

DASHBOARD_HOST = "http://127.0.0.1:3482"
BRAIN_HOST = "http://127.0.0.1:3483"
KNOWLEDGE_HOST = "http://127.0.0.1:3484"
WHATSAPP_HOST = "http://127.0.0.1:3478"
CRON_HOST = "http://127.0.0.1:3479"
CORTEX_HOST = "http://127.0.0.1:3485"

# ─── Infrastructure Constants ───

NEXTCLOUD_API_HOST = "http://192.168.1.100:3481"
TAILSCALE_PI_IP = "100.108.180.118"
TAILSCALE_PC_IP = "100.75.130.64"
PG_HOST = "100.108.180.118"
PG_PORT = 5432
PG_DB = "rajsadan"
PG_USER = "mrv"


# ─── Utilities ───


class BootReport:
    """Collects step results for the final boot report. Thread-safe."""

    def __init__(self):
        self.steps = []
        self.start_time = time.time()
        self.warnings = []
        self._lock = threading.Lock()

    def step(self, name, status, detail=""):
        elapsed = time.time() - self.start_time
        entry = {
            "name": name,
            "status": status,
            "detail": detail,
            "elapsed": f"{elapsed:.1f}s",
        }
        with self._lock:
            self.steps.append(entry)
        icon = {"OK": "+", "SKIP": "~", "WARN": "!", "FAIL": "x"}[status]
        print(f"  [{icon}] {name}", end="")
        if detail:
            print(f" -- {detail}")
        else:
            print()

    def warn(self, msg):
        with self._lock:
            self.warnings.append(msg)

    def summary(self):
        total = time.time() - self.start_time
        with self._lock:
            ok = sum(1 for s in self.steps if s["status"] == "OK")
            skip = sum(1 for s in self.steps if s["status"] == "SKIP")
            warn = sum(1 for s in self.steps if s["status"] == "WARN")
            fail = sum(1 for s in self.steps if s["status"] == "FAIL")

        print()
        print("=" * 50)
        print(f"  Boot Report: {ok} OK, {warn} WARN, {skip} SKIP, {fail} FAIL")
        print(f"  Total time: {total:.1f}s")
        if self.warnings:
            print(f"  Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                print(f"    ! {w}")
        print("=" * 50)

        return fail == 0


def ollama_api(endpoint, method="GET", data=None, timeout=10):
    """Make a request to the Ollama API."""
    url = f"{OLLAMA_HOST}{endpoint}"
    req = urllib.request.Request(url, method=method)
    if data:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def ollama_generate(model, prompt, system_prompt=None, timeout=120):
    """Generate text using a local LLM via Ollama. Returns the response text."""
    data = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3},
    }
    if system_prompt:
        data["system"] = system_prompt

    url = f"{OLLAMA_HOST}/api/generate"
    req = urllib.request.Request(url, method="POST")
    req.data = json.dumps(data).encode()
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
            return result.get("response", "")
    except (urllib.error.URLError, TimeoutError):
        return None


def _resolve_model(tags):
    """Pick the best available model from the boot model preferences."""
    if not tags:
        return None
    available = [m["name"] for m in tags.get("models", [])]
    if BOOT_MODEL in available:
        return BOOT_MODEL
    if BOOT_MODEL_FALLBACK in available:
        return BOOT_MODEL_FALLBACK
    return None


def _service_health(host, timeout=5):
    """Generic HTTP health check. Returns parsed JSON or None."""
    url = f"{host}/health"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _whatsapp_send_group(host, group, message):
    """Send a message to a WhatsApp group by key."""
    url = f"{host}/send-group"
    req = urllib.request.Request(url, method="POST")
    req.data = json.dumps({"group": group, "message": message}).encode()
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _start_node_service(service_dir, server_file, service_name, report, verbose,
                        health_host, health_key="status", health_value="running",
                        poll_seconds=15, extra_detail_fn=None, env_extra=None):
    """
    Generic helper to start a Node.js service and poll its /health endpoint.
    Returns True if service is running after the poll window.
    env_extra: optional dict of extra environment variables to pass to the process.
    """
    node_bin = shutil.which("node")
    if not node_bin:
        report.step(service_name, "FAIL", "node binary not found in PATH")
        return False

    if verbose:
        print(f"    Starting {service_name.lower()}...")

    proc_env = None
    if env_extra:
        proc_env = {**os.environ, **env_extra}

    try:
        if platform.system() == "Windows":
            subprocess.Popen(
                [node_bin, str(server_file)],
                cwd=str(service_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                env=proc_env,
            )
        else:
            subprocess.Popen(
                [node_bin, str(server_file)],
                cwd=str(service_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=proc_env,
            )

        # Poll /health
        for i in range(poll_seconds):
            time.sleep(1)
            health = _service_health(health_host)
            if health and health.get(health_key) == health_value:
                detail = extra_detail_fn(health) if extra_detail_fn else "started"
                report.step(service_name, "OK", detail)
                return True

            if verbose and i % 5 == 4:
                print(f"    waiting... ({i + 1}s)")

        report.step(service_name, "WARN", f"started but not responding after {poll_seconds}s")
        report.warn(f"{service_name} started but not responding")
        return True  # Non-blocking

    except OSError as e:
        report.step(service_name, "FAIL", f"could not start: {e}")
        return False


def _open_chrome(url):
    """Open a URL in Chrome (non-blocking, best-effort). WARN on failure, never raise."""
    try:
        if platform.system() == "Windows":
            subprocess.Popen(
                f'start chrome "{url}"',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            chrome_bins = ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]
            for bin_name in chrome_bins:
                if shutil.which(bin_name):
                    subprocess.Popen(
                        [bin_name, url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return
    except OSError:
        pass


def _load_knowledge_credentials():
    """Parse memory/knowledge/credentials.toml and return dict of env vars."""
    creds_file = PROJECT_ROOT / "memory" / "knowledge" / "credentials.toml"
    if not creds_file.is_file():
        return {}

    env_vars = {}
    section_key = None
    section_env = None

    for line in creds_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # New section -- flush previous
        if line.startswith("["):
            if section_env and section_key:
                env_vars[section_env] = section_key
            section_key = None
            section_env = None
            continue

        if "=" not in line:
            continue

        field, _, value = line.partition("=")
        field = field.strip()
        value = value.strip().strip('"')

        if field == "env_var":
            section_env = value
        elif field in ("key", "access_token") and value:
            section_key = value

    # Flush last section
    if section_env and section_key:
        env_vars[section_env] = section_key

    return env_vars


def _nextcloud_api_health(timeout=5):
    """Direct HTTP health check for the NextCloud API Service on the Pi."""
    return _service_health(NEXTCLOUD_API_HOST, timeout=timeout)


def _find_pid_on_port(port):
    """Find the PID of a process listening on the given port. Returns PID or None."""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                pid_str = result.stdout.strip().splitlines()[0].strip()
                if pid_str.isdigit():
                    return int(pid_str)
        except (subprocess.TimeoutExpired, OSError):
            pass
    else:
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                pid_str = result.stdout.strip().splitlines()[0].strip()
                if pid_str.isdigit():
                    return int(pid_str)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    return None


def _kill_pid(pid, verbose=False):
    """Kill a process by PID. Returns True if successful."""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid), "/T"],
                    capture_output=True, text=True, timeout=10,
                )
            return True
        except (subprocess.TimeoutExpired, OSError):
            return False
    else:
        try:
            os.kill(pid, 15)  # SIGTERM
            time.sleep(1)
            try:
                os.kill(pid, 0)  # Check if still alive
                os.kill(pid, 9)  # SIGKILL
            except OSError:
                pass  # Already dead
            return True
        except OSError:
            return False


def _cleanup_previous_session(report, dry_run=False, verbose=False):
    """
    Clean up zombie services from a previous unclean shutdown.
    Kills any processes still listening on Raj Sadan's known service ports.
    Runs before new services are started (Step 3).
    """
    step_name = "2b. Cleanup Previous Session"

    if dry_run:
        report.step(step_name, "SKIP", "dry-run mode")
        return

    SERVICE_PORTS = {
        "WhatsApp": 3478,
        "Cron": 3479,
        "Content-Engine": 3480,
        "Dashboard": 3482,
        "Senses": 3483,
        "Knowledge": 3484,
        "Cortex": 3485,
    }

    killed = []
    failed = []

    for name, port in SERVICE_PORTS.items():
        pid = _find_pid_on_port(port)
        if pid:
            if verbose:
                print(f"    Zombie found: {name} on port {port} (PID {pid})")
            if _kill_pid(pid, verbose):
                killed.append(f"{name}:{port}")
            else:
                failed.append(f"{name}:{port}(PID {pid})")
                report.warn(f"Could not kill zombie {name} on port {port} (PID {pid})")

    # Clean stale boot lockfile if it references a dead process
    if BOOT_LOCKFILE.exists():
        try:
            old_pid = int(BOOT_LOCKFILE.read_text().strip())
            if old_pid != os.getpid():
                # Check if the old process is dead
                if platform.system() == "Windows":
                    result = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {old_pid}", "/NH"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if str(old_pid) not in result.stdout:
                        BOOT_LOCKFILE.unlink(missing_ok=True)
                        if verbose:
                            print(f"    Removed stale lockfile (PID {old_pid} dead)")
                else:
                    try:
                        os.kill(old_pid, 0)
                    except OSError:
                        BOOT_LOCKFILE.unlink(missing_ok=True)
                        if verbose:
                            print(f"    Removed stale lockfile (PID {old_pid} dead)")
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass

    if killed:
        report.step(step_name, "OK", f"killed {len(killed)} zombies: {', '.join(killed)}")
    elif failed:
        report.step(step_name, "WARN", f"failed to kill: {', '.join(failed)}")
    else:
        report.step(step_name, "OK", "no zombies — clean slate")


# ─── Boot Steps ───


def step_0_credentials(report, dry_run=False, verbose=False):
    """Step 0: Load system credentials vault into environment."""
    step_name = "0. Load Credentials"
    if dry_run:
        report.step(step_name, "SKIP", "dry-run mode")
        return True

    t0 = time.time()
    vault_paths = [
        PROJECT_ROOT / "immunity" / "vault" / "credentials.toml",
        PROJECT_ROOT / "memory" / "knowledge" / "credentials.toml",
    ]

    total_loaded = 0
    for vault_path in vault_paths:
        if not vault_path.exists():
            if verbose:
                report.step(step_name, "INFO", f"not found: {vault_path.name}")
            continue

        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                report.step(step_name, "WARN", "no TOML parser available")
                return True

        try:
            with open(vault_path, "rb") as f:
                creds = tomllib.load(f)

            for section_name, section in creds.items():
                if not isinstance(section, dict):
                    continue
                env_var = section.get("env_var")
                key = section.get("key")
                if env_var and key and str(key).strip():
                    os.environ[env_var] = str(key)
                    total_loaded += 1
                    if verbose:
                        report.step(step_name, "OK", f"  {env_var} loaded from {vault_path.name}")
        except Exception as e:
            report.step(step_name, "WARN", f"failed to parse {vault_path.name}: {e}")
            continue

    elapsed = time.time() - t0
    report.step(step_name, "OK", f"{total_loaded} credentials loaded ({elapsed:.1f}s)")
    return True


def step_1_ollama(report, dry_run=False):
    """Step 1: Check if Ollama is running. If not, attempt to start it. BLOCKING."""
    if dry_run:
        report.step("1. Ollama", "SKIP", "dry-run mode")
        return True

    t0 = time.time()

    # Check if Ollama is already running
    tags = ollama_api("/api/tags")
    if tags is not None:
        model_count = len(tags.get("models", []))
        elapsed = time.time() - t0
        report.step("1. Ollama", "OK", f"running, {model_count} models ({elapsed:.1f}s)")
        return True

    # Ollama not running -- try to start it
    print("  [ ] Ollama not running. Attempting to start...")

    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        report.step("1. Ollama", "FAIL", "ollama binary not found in PATH")
        return False

    try:
        if platform.system() == "Windows":
            subprocess.Popen(
                [ollama_bin, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                [ollama_bin, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

        # Wait for Ollama to be ready (up to 15 seconds)
        for _ in range(15):
            time.sleep(1)
            tags = ollama_api("/api/tags")
            if tags is not None:
                model_count = len(tags.get("models", []))
                elapsed = time.time() - t0
                report.step(
                    "1. Ollama", "OK",
                    f"started, {model_count} models ({elapsed:.1f}s)",
                )
                return True

        report.step("1. Ollama", "FAIL", "started but not responding after 15s")
        return False

    except OSError as e:
        report.step("1. Ollama", "FAIL", f"could not start: {e}")
        return False


def step_2_detect_boot_mode(report, dry_run=False, verbose=False):
    """Step 2: Detect boot mode by reading state/session.toml.
    Returns (mode, session_data, crash_decisions).
    mode is one of: 'clean', 'crash-recovery', 'cold-start'
    """
    if dry_run:
        report.step("2. Boot Mode", "SKIP", "dry-run mode")
        return "cold-start", None, []

    t0 = time.time()

    if not SESSION_FILE.exists():
        elapsed = time.time() - t0
        report.step("2. Boot Mode", "OK", f"cold-start (no session.toml) ({elapsed:.1f}s)")
        return "cold-start", None, []

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(SESSION_FILE, "rb") as f:
            session_data = tomllib.load(f)
    except Exception as e:
        elapsed = time.time() - t0
        report.step("2. Boot Mode", "WARN", f"cold-start (corrupt session.toml: {e})")
        report.warn(f"session.toml unreadable: {e}")
        return "cold-start", None, []

    clean_shutdown = session_data.get("clean_shutdown", False)

    if clean_shutdown:
        elapsed = time.time() - t0
        uptime = session_data.get("uptime_minutes", "?")
        report.step("2. Boot Mode", "OK", f"clean (last session: {uptime}min uptime) ({elapsed:.1f}s)")
        return "clean", session_data, []

    # Crash recovery -- also read recent decisions after last state
    crash_decisions = []
    updated_at = session_data.get("updated_at", "")
    if updated_at and DECISIONS_LOG.exists():
        try:
            # Read last 50 lines of decisions log
            lines = DECISIONS_LOG.read_text(encoding="utf-8").strip().splitlines()
            tail = lines[-50:] if len(lines) > 50 else lines

            for line in tail:
                try:
                    decision = json.loads(line)
                    ts = decision.get("timestamp", "")
                    if ts and ts > updated_at:
                        crash_decisions.append(decision)
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

    elapsed = time.time() - t0
    gap_info = ""
    if updated_at:
        try:
            last_ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).replace(tzinfo=None)
            gap_minutes = (datetime.now() - last_ts).total_seconds() / 60
            gap_info = f", {gap_minutes:.0f}min gap"
        except (ValueError, TypeError):
            pass

    decision_info = f", {len(crash_decisions)} post-crash decisions" if crash_decisions else ""
    report.step("2. Boot Mode", "WARN",
                f"crash-recovery (unclean shutdown{gap_info}{decision_info}) ({elapsed:.1f}s)")
    report.warn("System did not shut down cleanly")

    return "crash-recovery", session_data, crash_decisions


# ─── Step 3: Parallel Service Starts ───


def _start_dashboard(report, verbose):
    """Start Dashboard service (port 3482). Thread-safe."""
    dashboard_dir = PROJECT_ROOT / "mind" / "dashboard"
    server_js = dashboard_dir / "server.js"

    if not server_js.is_file():
        report.step("3. Dashboard", "SKIP", "service not installed")
        return "skip"

    if not (dashboard_dir / "node_modules").is_dir():
        report.step("3. Dashboard", "WARN", "run 'npm install' in mind/dashboard/")
        return "warn"

    # Check if already running
    health = _service_health(DASHBOARD_HOST)
    if health and health.get("status") == "running":
        report.step("3. Dashboard", "OK", f"already running at {DASHBOARD_HOST}")
        _open_chrome(DASHBOARD_HOST)
        return "ok"

    started = _start_node_service(
        service_dir=dashboard_dir,
        server_file=server_js,
        service_name="3. Dashboard",
        report=report,
        verbose=verbose,
        health_host=DASHBOARD_HOST,
        health_key="status",
        health_value="running",
        poll_seconds=15,
        extra_detail_fn=lambda h: f"running at {DASHBOARD_HOST}",
    )

    if started:
        _open_chrome(DASHBOARD_HOST)
    return "ok" if started else "fail"


def _start_brain(report, verbose):
    """Start Brain service (port 3483). Thread-safe."""
    brain_dir = PROJECT_ROOT / "mind" / "brain"
    server_js = brain_dir / "server.js"

    if not server_js.is_file():
        report.step("3. Brain", "SKIP", "service not installed")
        return "skip"

    if not (brain_dir / "node_modules").is_dir():
        report.step("3. Brain", "WARN", "run 'npm install' in mind/brain/")
        return "warn"

    health = _service_health(BRAIN_HOST)
    if health and health.get("status") == "running":
        report.step("3. Brain", "OK", f"already running at {BRAIN_HOST}")
        return "ok"

    _start_node_service(
        service_dir=brain_dir,
        server_file=server_js,
        service_name="3. Brain",
        report=report,
        verbose=verbose,
        health_host=BRAIN_HOST,
        health_key="status",
        health_value="running",
        poll_seconds=15,
        extra_detail_fn=lambda h: f"model: {h.get('model', '?')}",
    )
    return "ok"


def _start_knowledge(report, verbose):
    """Start Knowledge service (port 3484) with API keys. Thread-safe."""
    knowledge_dir = PROJECT_ROOT / "memory" / "knowledge"
    server_js = knowledge_dir / "server.js"

    if not server_js.is_file():
        report.step("3. Knowledge", "SKIP", "service not installed")
        return "skip"

    if not (knowledge_dir / "node_modules").is_dir():
        report.step("3. Knowledge", "WARN", "run 'npm install' in memory/knowledge/")
        return "warn"

    health = _service_health(KNOWLEDGE_HOST)
    if health and health.get("status") == "running":
        caps = health.get("capabilities", "?")
        enabled = health.get("enabled", "?")
        report.step("3. Knowledge", "OK", f"already running -- {caps} capabilities, {enabled} enabled")
        return "ok"

    env_extra = _load_knowledge_credentials()
    key_count = len(env_extra)

    _start_node_service(
        service_dir=knowledge_dir,
        server_file=server_js,
        service_name="3. Knowledge",
        report=report,
        verbose=verbose,
        health_host=KNOWLEDGE_HOST,
        health_key="status",
        health_value="running",
        poll_seconds=15,
        extra_detail_fn=lambda h: f"{h.get('capabilities', '?')} capabilities, {h.get('enabled', '?')} enabled, {key_count} API keys",
        env_extra=env_extra,
    )
    return "ok"


def _start_whatsapp(report, verbose, no_whatsapp=False):
    """Start WhatsApp service (port 3478). Thread-safe."""
    if no_whatsapp:
        report.step("3. WhatsApp", "SKIP", "disabled via --no-whatsapp")
        return "skip"

    whatsapp_dir = PROJECT_ROOT / "senses" / "audition" / "whatsapp"
    server_js = whatsapp_dir / "server.js"

    if not server_js.is_file():
        report.step("3. WhatsApp", "SKIP", "service not installed")
        report.warn("WhatsApp service not found (senses/audition/whatsapp/server.js)")
        return "skip"

    if not (whatsapp_dir / "node_modules").is_dir():
        report.step("3. WhatsApp", "WARN", "run 'npm install' in senses/audition/whatsapp/")
        return "warn"

    # Check if already running and connected
    health = _service_health(WHATSAPP_HOST)
    if health and health.get("status") == "connected":
        groups = health.get("groups", {})
        responder = ", responder ON" if health.get("responder") else ""
        report.step(
            "3. WhatsApp", "OK",
            f"already running, {groups.get('ready', 0)}/{groups.get('total', 0)} groups{responder}"
        )
        _whatsapp_send_group(WHATSAPP_HOST, "general", "Raj Sadan is online. Gateway open.")
        return "ok"

    # Not running -- start the service with special WhatsApp polling
    node_bin = shutil.which("node")
    if not node_bin:
        report.step("3. WhatsApp", "FAIL", "node binary not found in PATH")
        return "fail"

    if verbose:
        print("    Starting WhatsApp service...")

    try:
        if platform.system() == "Windows":
            subprocess.Popen(
                [node_bin, str(server_js)],
                cwd=str(whatsapp_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                [node_bin, str(server_js)],
                cwd=str(whatsapp_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

        # Poll /health until connected (up to 20 seconds)
        for i in range(20):
            time.sleep(1)
            health = _service_health(WHATSAPP_HOST)
            if health:
                status = health.get("status", "unknown")
                if status == "connected":
                    groups = health.get("groups", {})
                    responder = ", responder ON" if health.get("responder") else ""
                    report.step(
                        "3. WhatsApp", "OK",
                        f"started, {groups.get('ready', 0)}/{groups.get('total', 0)} groups{responder}"
                    )
                    _whatsapp_send_group(
                        WHATSAPP_HOST, "general",
                        "Raj Sadan is online. Gateway open."
                    )
                    return "ok"
                elif status == "waiting_for_qr":
                    report.step(
                        "3. WhatsApp", "WARN",
                        "needs QR scan -- run 'node senses/audition/whatsapp/server.js' manually"
                    )
                    report.warn("WhatsApp requires first-time QR code scan")
                    return "warn"

            if verbose and i % 5 == 4:
                print(f"    waiting... ({i + 1}s)")

        report.step("3. WhatsApp", "WARN", "started but not connected after 20s")
        report.warn("WhatsApp service started but connection not established")
        return "warn"

    except OSError as e:
        report.step("3. WhatsApp", "FAIL", f"could not start: {e}")
        return "fail"


def _start_cron(report, verbose, no_cron=False):
    """Start Cron service (port 3479). Thread-safe."""
    if no_cron:
        report.step("3. Cron", "SKIP", "disabled via --no-cron")
        return "skip"

    cron_dir = PROJECT_ROOT / "senses" / "chronoception" / "cron"
    server_js = cron_dir / "server.js"

    if not server_js.is_file():
        report.step("3. Cron", "SKIP", "service not installed")
        report.warn("Cron service not found (senses/chronoception/cron/server.js)")
        return "skip"

    if not (cron_dir / "node_modules").is_dir():
        report.step("3. Cron", "WARN", "run 'npm install' in senses/chronoception/cron/")
        report.warn("Cron service dependencies not installed")
        return "warn"

    health = _service_health(CRON_HOST)
    if health and health.get("status") == "running":
        jobs = health.get("jobs", {})
        report.step(
            "3. Cron", "OK",
            f"already running, {jobs.get('active', 0)} active / {jobs.get('total', 0)} total"
        )
        return "ok"

    def _cron_detail(h):
        jobs = h.get("jobs", {})
        return f"started, {jobs.get('active', 0)} active / {jobs.get('total', 0)} total"

    _start_node_service(
        service_dir=cron_dir,
        server_file=server_js,
        service_name="3. Cron",
        report=report,
        verbose=verbose,
        health_host=CRON_HOST,
        health_key="status",
        health_value="running",
        poll_seconds=15,
        extra_detail_fn=_cron_detail,
    )
    return "ok"


def step_3_parallel_services(report, dry_run=False, verbose=False,
                             no_whatsapp=False, no_cron=False):
    """Step 3: Start all 5 services in PARALLEL using ThreadPoolExecutor.
    Returns dict of service -> result status."""
    if dry_run:
        report.step("3. Parallel Services", "SKIP", "dry-run mode")
        return {}

    t0 = time.time()
    print("  [ ] Starting 5 services in parallel...")

    results = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_start_dashboard, report, verbose): "dashboard",
            executor.submit(_start_brain, report, verbose): "brain",
            executor.submit(_start_knowledge, report, verbose): "knowledge",
            executor.submit(_start_whatsapp, report, verbose, no_whatsapp): "whatsapp",
            executor.submit(_start_cron, report, verbose, no_cron): "cron",
        }

        for future in as_completed(futures, timeout=25):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = "fail"
                report.step(f"3. {name.title()}", "FAIL", f"exception: {e}")
                report.warn(f"{name.title()} thread crashed: {e}")

    elapsed = time.time() - t0
    ok_count = sum(1 for v in results.values() if v == "ok")
    total = len(results)
    print(f"  [+] Parallel services: {ok_count}/{total} OK ({elapsed:.1f}s)")

    return results


# ─── Step 4: Parallel Infrastructure Checks ───


def _check_pi_ssh(report, verbose):
    """Check Raspberry Pi SSH + NextCloud. Thread-safe."""
    pi_config = PROJECT_ROOT / "senses" / "proprioception" / "pi" / "config.toml"
    if not pi_config.is_file():
        report.step("4. Pi SSH", "SKIP", "no Pi config (senses/proprioception/pi/config.toml)")
        return "skip"

    # Read SSH alias from config or use default
    ssh_alias = "rajsadan-pi"
    ssh_timeout = "5"
    try:
        raw = pi_config.read_text(encoding="utf-8")
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("ssh_alias"):
                ssh_alias = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("timeout"):
                ssh_timeout = line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass

    if verbose:
        print(f"    SSH target: {ssh_alias} (timeout: {ssh_timeout}s)")

    # Test SSH connectivity
    ssh_ok = False
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", f"ConnectTimeout={ssh_timeout}",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes",
                ssh_alias,
                "echo ok",
            ],
            capture_output=True,
            text=True,
            timeout=int(ssh_timeout) + 5,
        )

        if result.returncode != 0 or result.stdout.strip() != "ok":
            report.step(
                "4. Pi SSH", "WARN",
                f"Pi unreachable ({ssh_alias}) -- continuing without cloud storage"
            )
            report.warn(f"Raspberry Pi not reachable via SSH ({ssh_alias})")
            return "warn"
        else:
            ssh_ok = True

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        report.step("4. Pi SSH", "WARN", f"SSH failed -- {e}")
        report.warn(f"SSH to Pi failed: {e}")
        return "warn"

    if ssh_ok:
        if verbose:
            print("    SSH connected. Checking NextCloud...")

        # Check NextCloud Docker via SSH
        nc_status = "unknown"
        try:
            nc_result = subprocess.run(
                [
                    "ssh",
                    "-o", f"ConnectTimeout={ssh_timeout}",
                    ssh_alias,
                    "curl -sf http://localhost:8080/status.php",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if nc_result.returncode == 0 and nc_result.stdout.strip():
                try:
                    status_json = json.loads(nc_result.stdout.strip())
                    version = status_json.get("versionstring", "unknown")
                    nc_status = f"NextCloud v{version} running"
                except json.JSONDecodeError:
                    nc_status = "NextCloud responded (non-JSON)"
            else:
                nc_status = "NextCloud not responding"

        except (subprocess.TimeoutExpired, OSError):
            nc_status = "NextCloud check timed out"

        # Check Docker containers
        docker_info = ""
        try:
            docker_result = subprocess.run(
                [
                    "ssh",
                    "-o", f"ConnectTimeout={ssh_timeout}",
                    ssh_alias,
                    "docker ps --format '{{.Names}}' 2>/dev/null | wc -l",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if docker_result.returncode == 0:
                count = docker_result.stdout.strip()
                docker_info = f", {count} containers"
        except (subprocess.TimeoutExpired, OSError):
            pass

        if "not responding" in nc_status or "timed out" in nc_status:
            report.step("4. Pi SSH", "WARN", f"Pi connected. {nc_status}")
            report.warn(f"Pi SSH OK but {nc_status}")
        else:
            report.step("4. Pi SSH", "OK", f"Pi connected. {nc_status}{docker_info}")

    # NextCloud API Service health check (direct HTTP -- no SSH needed)
    if verbose:
        print(f"    Checking NextCloud API at {NEXTCLOUD_API_HOST}...")

    nc_api = _nextcloud_api_health(timeout=5)
    if nc_api and nc_api.get("status") == "ok":
        domains = nc_api.get("domains", [])
        domain_info = f", domains: {', '.join(domains)}" if domains else ""
        report.step("4. NextCloud API", "OK", f"running at {NEXTCLOUD_API_HOST}{domain_info}")
    else:
        # Best-effort restart via SSH if we have connectivity
        restarted = False
        if ssh_ok:
            try:
                restart_result = subprocess.run(
                    [
                        "ssh",
                        "-o", f"ConnectTimeout={ssh_timeout}",
                        ssh_alias,
                        "systemctl --user restart nextcloud-api.service 2>/dev/null || "
                        "systemctl restart nextcloud-api.service 2>/dev/null || "
                        "echo 'restart-failed'",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if "restart-failed" not in restart_result.stdout:
                    for _ in range(10):
                        time.sleep(1)
                        nc_api = _nextcloud_api_health(timeout=3)
                        if nc_api and nc_api.get("status") == "ok":
                            report.step("4. NextCloud API", "OK", "restarted via SSH, now healthy")
                            restarted = True
                            break
            except (subprocess.TimeoutExpired, OSError):
                pass

        if not restarted:
            report.step("4. NextCloud API", "WARN", f"not responding at {NEXTCLOUD_API_HOST}")
            report.warn("NextCloud API Service not reachable")

    return "ok"


def _check_tailscale(report, verbose):
    """Check Tailscale mesh connectivity. Thread-safe."""
    tailscale_bin = shutil.which("tailscale")
    if not tailscale_bin:
        report.step("4. Tailscale", "WARN", "tailscale binary not found in PATH")
        report.warn("Tailscale not installed or not in PATH")
        return "warn"

    if verbose:
        print("    Checking Tailscale status...")

    try:
        result = subprocess.run(
            [tailscale_bin, "status", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            report.step("4. Tailscale", "WARN", "tailscale status failed")
            report.warn(f"Tailscale status exited with code {result.returncode}")
            return "warn"

        status = json.loads(result.stdout)
        peers = status.get("Peer", {})
        self_ip = status.get("Self", {}).get("TailscaleIPs") or []

        pc_ok = any(TAILSCALE_PC_IP in str(ip) for ip in self_ip)

        pi_ok = False
        for peer_key, peer in peers.items():
            peer_ips = peer.get("TailscaleIPs") or []
            if any(TAILSCALE_PI_IP in str(ip) for ip in peer_ips):
                pi_ok = peer.get("Online", False)
                break

        parts = []
        if pc_ok:
            parts.append(f"PC {TAILSCALE_PC_IP}")
        if pi_ok:
            parts.append(f"Pi {TAILSCALE_PI_IP}")

        if pc_ok and pi_ok:
            report.step("4. Tailscale", "OK", f"mesh connected: {', '.join(parts)}")
        elif pc_ok or pi_ok:
            missing = "Pi" if not pi_ok else "PC"
            report.step("4. Tailscale", "WARN", f"partial: {', '.join(parts)} ({missing} not online)")
            report.warn(f"Tailscale: {missing} node not online")
        else:
            report.step("4. Tailscale", "WARN", "connected but neither Pi nor PC IPs found in mesh")
            report.warn("Tailscale running but expected nodes not found")

    except subprocess.TimeoutExpired:
        report.step("4. Tailscale", "WARN", "tailscale status timed out")
        report.warn("Tailscale status command timed out after 10s")
    except (json.JSONDecodeError, OSError) as e:
        report.step("4. Tailscale", "WARN", f"check failed: {e}")
        report.warn(f"Tailscale check error: {e}")

    return "ok"


def _check_postgresql(report, verbose):
    """Check PostgreSQL connectivity. Thread-safe."""
    import socket

    if verbose:
        print(f"    Checking PostgreSQL at {PG_HOST}:{PG_PORT}...")

    # TCP connectivity check
    try:
        sock = socket.create_connection((PG_HOST, PG_PORT), timeout=5)
        sock.close()
    except (socket.timeout, OSError) as e:
        report.step("4. PostgreSQL", "WARN", f"port {PG_PORT} unreachable at {PG_HOST}: {e}")
        report.warn(f"PostgreSQL not reachable at {PG_HOST}:{PG_PORT}")
        return "warn"

    # Try an actual pg query if psql is available
    psql_bin = shutil.which("psql")
    pg_pass = os.environ.get("PGPASSWORD", "")
    if not pg_pass:
        db_url = os.environ.get("RAJSADAN_DATABASE_URL", "")
        if "@" in db_url and ":" in db_url:
            try:
                pg_pass = db_url.split("://")[1].split("@")[0].split(":")[1]
            except (IndexError, ValueError):
                pass

    if psql_bin and pg_pass:
        try:
            result = subprocess.run(
                [psql_bin, "-h", PG_HOST, "-p", str(PG_PORT),
                 "-U", PG_USER, "-d", PG_DB, "-c", "SELECT 1;"],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "PGPASSWORD": pg_pass},
            )
            if result.returncode == 0:
                report.step("4. PostgreSQL", "OK",
                            f"connected to {PG_DB}@{PG_HOST}:{PG_PORT} as {PG_USER}")
            else:
                stderr_short = (result.stderr or "").strip()[:80]
                report.step("4. PostgreSQL", "WARN",
                            f"port open but psql query failed: {stderr_short}")
                report.warn(f"PostgreSQL port open but query failed: {stderr_short}")
        except subprocess.TimeoutExpired:
            report.step("4. PostgreSQL", "WARN", "psql query timed out (port is open)")
            report.warn("PostgreSQL psql query timed out after 10s")
        except OSError as e:
            report.step("4. PostgreSQL", "WARN", f"psql failed: {e}")
            report.warn(f"PostgreSQL psql error: {e}")
    elif psql_bin and not pg_pass:
        report.step("4. PostgreSQL", "OK",
                     f"port {PG_PORT} open at {PG_HOST} (no PGPASSWORD, skipping auth)")
    else:
        report.step("4. PostgreSQL", "OK",
                     f"port {PG_PORT} open at {PG_HOST} (psql not available)")

    return "ok"


def step_4_parallel_infrastructure(report, dry_run=False, verbose=False):
    """Step 4: Run infrastructure checks in PARALLEL using ThreadPoolExecutor."""
    if dry_run:
        report.step("4. Infrastructure", "SKIP", "dry-run mode")
        return {}

    t0 = time.time()
    print("  [ ] Infrastructure checks in parallel...")

    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_check_pi_ssh, report, verbose): "pi_ssh",
            executor.submit(_check_tailscale, report, verbose): "tailscale",
            executor.submit(_check_postgresql, report, verbose): "postgresql",
        }

        for future in as_completed(futures, timeout=30):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = "fail"
                report.step(f"4. {name}", "WARN", f"check exception: {e}")
                report.warn(f"Infrastructure check {name} crashed: {e}")

    elapsed = time.time() - t0
    print(f"  [+] Infrastructure checks done ({elapsed:.1f}s)")

    return results


# ─── Step 5: Cortex (starts LAST) ───


def step_5_cortex(report, dry_run=False, verbose=False):
    """Step 5: Start Cortex Autonomous Supervisor (LAST service) + handover."""
    if dry_run:
        report.step("5. Cortex", "SKIP", "dry-run mode")
        return True

    t0 = time.time()
    cortex_dir = PROJECT_ROOT / "mind" / "cortex"
    server_js = cortex_dir / "server.js"

    if not server_js.is_file():
        report.step("5. Cortex", "SKIP", "service not installed")
        return True

    # Check if already running
    health = _service_health(CORTEX_HOST)
    if health and health.get("status") == "running":
        monitored = health.get("services_monitored", "?")
        report.step("5. Cortex", "OK", f"already running, monitoring {monitored} services")
    else:
        def _cortex_detail(h):
            return f"monitoring {h.get('services_monitored', '?')} services"

        _start_node_service(
            service_dir=cortex_dir,
            server_file=server_js,
            service_name="5. Cortex",
            report=report,
            verbose=verbose,
            health_host=CORTEX_HOST,
            health_key="status",
            health_value="running",
            poll_seconds=10,
            extra_detail_fn=_cortex_detail,
        )

    # Handover -- POST /notify/boot-complete
    health = _service_health(CORTEX_HOST)
    if not health or health.get("status") != "running":
        report.step("5. Cortex Handover", "WARN", "Cortex not running -- cannot hand over")
        report.warn("Cortex handover skipped -- service not responding")
        return True

    boot_time = f"{time.time() - report.start_time:.1f}"
    warnings = sum(1 for s in report.steps if s["status"] == "WARN")

    try:
        payload = json.dumps({
            "boot_time": boot_time,
            "warnings": warnings,
            "timestamp": datetime.now().isoformat(),
        }).encode()

        req = urllib.request.Request(
            f"{CORTEX_HOST}/notify/boot-complete",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                elapsed = time.time() - t0
                report.step("5. Cortex Handover", "OK",
                            f"Cortex in control -- {result.get('services_monitored', '?')} services ({elapsed:.1f}s)")
            else:
                report.step("5. Cortex Handover", "WARN", "Cortex responded but handover unclear")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        report.step("5. Cortex Handover", "WARN", f"handover signal failed: {e}")
        report.warn(f"Cortex handover failed: {e}")

    return True


# ─── Step 6: Scrum Board ───


def step_6_scrum(report, dry_run=False, verbose=False):
    """Step 6: Evaluate active plans and generate a scrum board summary."""
    if dry_run:
        report.step("6. Scrum Board", "SKIP", "dry-run mode")
        return None

    t0 = time.time()
    plans_dir = PROJECT_ROOT / "plans"
    if not plans_dir.is_dir():
        report.step("6. Scrum Board", "SKIP", "no plans/ directory")
        return None

    try:
        from mind.sherpa.scrum.main import evaluate_for_boot
        summary, plan_count, actionable = evaluate_for_boot(verbose)

        if summary is None:
            report.step("6. Scrum Board", "SKIP", "no active plans")
            return None

        elapsed = time.time() - t0
        report.step(
            "6. Scrum Board", "OK",
            f"{plan_count} plan(s), {actionable} actionable task(s) ({elapsed:.1f}s)"
        )
        return summary

    except Exception as e:
        report.step("6. Scrum Board", "WARN", f"scrum evaluation failed: {e}")
        report.warn(f"Scrum board generation failed: {e}")
        return None


# ─── Step 7: Build Boot Prompt ───


def _build_session_resume_prompt(boot_mode, session_data, crash_decisions, scrum_summary):
    """Build a structured boot prompt from session.toml data (clean/crash-recovery).
    Pure string formatting -- no LLM call."""

    # Extract fields with safe defaults
    updated_at = session_data.get("updated_at", "unknown")
    uptime = session_data.get("uptime_minutes", "?")
    services = session_data.get("services", {})
    cortex = session_data.get("cortex", {})
    plans = session_data.get("plans", {})
    resources = session_data.get("resources", {})
    phone = session_data.get("phone")
    alerts = session_data.get("alerts", [])
    recent_decisions = session_data.get("recent_decisions", [])
    memo = session_data.get("memo", "")

    # Service summary
    UP_STATES = {"up", "healthy", "running", "ok", "connected"}
    svc_up = sum(1 for s in services.values() if isinstance(s, dict) and s.get("status", "").lower() in UP_STATES)
    svc_total = len(services)
    svc_details = ", ".join(
        f"{name}={'UP' if (isinstance(info, dict) and info.get('status', '').lower() in UP_STATES) else 'DOWN'}"
        for name, info in services.items()
    ) if services else "none"

    # Cortex summary
    loop_count = cortex.get("loop_count", "?")
    decisions_today = cortex.get("decisions_today", "?")
    by_type = cortex.get("by_type", {})
    by_type_str = ", ".join(f"{k}:{v}" for k, v in by_type.items()) if by_type else "none"

    # Plan summary
    plan_lines = []
    for plan_id, pinfo in plans.items():
        if isinstance(pinfo, dict):
            plan_lines.append(
                f"  {plan_id}: {pinfo.get('done', 0)}/{pinfo.get('total', 0)} done, "
                f"{pinfo.get('in_progress', 0)} in-progress, {pinfo.get('pending', 0)} pending"
            )
    plan_block = "\n".join(plan_lines) if plan_lines else "  No plans tracked"

    # Resources
    cpu = resources.get("cpu", 0)
    ram = resources.get("ram", 0)
    vram = resources.get("vram", 0)

    # Phone
    if phone:
        phone_str = f"{phone.get('battery', '?')}% {'charging' if phone.get('charging') else 'discharging'}, {phone.get('network', '?')}"
    else:
        phone_str = "not connected"

    # Alerts
    alerts_str = ", ".join(alerts) if alerts else "none"

    # Recent decisions
    decision_lines = []
    for d in recent_decisions[:5]:
        if isinstance(d, dict):
            decision_lines.append(
                f"  [{d.get('time', '?')}] {d.get('type', '?')}/{d.get('target', '?')}: "
                f"{d.get('action', '?')} -> {d.get('outcome', '?')}"
            )

    # Build the prompt block
    lines = [
        f"SESSION RESUME (from state/session.toml):",
        f"  Boot mode: {boot_mode}",
        f"  Last session: {updated_at}, {uptime} min uptime",
        f"  Services at last check: {svc_up}/{svc_total} up ({svc_details})",
        f"  Cortex: {loop_count} loops, {decisions_today} decisions ({by_type_str})",
        f"  Plans:",
        plan_block,
        f"  Resources: CPU {cpu}%, RAM {ram}%, VRAM {vram}%",
        f"  Phone: {phone_str}",
        f"  Alerts: {alerts_str}",
    ]

    if memo:
        lines.append(f'  PM Memo: "{memo}"')

    if decision_lines:
        lines.append(f"  Recent decisions:")
        lines.extend(decision_lines)

    # Recent git work — always included so Mr. V knows what last session accomplished
    try:
        git_result = subprocess.run(
            ["git", "log", "--oneline", "--no-color", "-8"],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        if git_result.returncode == 0 and git_result.stdout.strip():
            lines.append("")
            lines.append("  Recent git work (last 8 commits):")
            for gl in git_result.stdout.strip().split("\n")[:8]:
                lines.append(f"    {gl}")
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass

    # Recent file changes — shows what was touched even without commits
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--stat", "--no-color", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        if diff_result.returncode == 0 and diff_result.stdout.strip():
            stat_lines = diff_result.stdout.strip().split("\n")
            # Show summary line (last line: "X files changed, Y insertions, Z deletions")
            if stat_lines:
                lines.append(f"  Uncommitted changes: {stat_lines[-1].strip()}")
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass

    # Crash-recovery warning
    if boot_mode == "crash-recovery":
        lines.append("")
        lines.append("  !! CRASH DETECTED -- system did not shut down cleanly")
        lines.append(f"  Last state from: {updated_at}")

        # Calculate gap
        try:
            last_ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).replace(tzinfo=None)
            gap_minutes = (datetime.now() - last_ts).total_seconds() / 60
            lines.append(f"  Time since last state: {gap_minutes:.0f} minutes ago")
        except (ValueError, TypeError):
            pass

        if crash_decisions:
            types = {}
            for d in crash_decisions:
                t = d.get("type", "unknown")
                types[t] = types.get(t, 0) + 1
            type_summary = ", ".join(f"{k}:{v}" for k, v in types.items())
            lines.append(f"  Decisions after last state: {len(crash_decisions)} ({type_summary})")
        else:
            lines.append("  Decisions after last state: 0 (clean data)")

    return "\n".join(lines)


def _load_checkpoint_for_boot(verbose=False):
    """
    Try to load the last checkpoint via gateway/checkpoint.cjs.
    Returns formatted checkpoint string if fresh (<48h), or None.
    NON-BLOCKING -- returns None on any failure.
    """
    gateway_script = PROJECT_ROOT / "senses" / "gateway" / "checkpoint.cjs"
    if not gateway_script.is_file():
        return None

    try:
        result = subprocess.run(
            ["node", str(gateway_script), "--load", "--format", "boot"],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        checkpoint = json.loads(result.stdout.strip())

        # Check staleness -- skip if older than 48 hours
        ts = checkpoint.get("timestamp", "")
        if ts:
            try:
                normalized = ts.replace("Z", "+00:00")
                cp_time = datetime.fromisoformat(normalized)
                cp_naive = cp_time.replace(tzinfo=None)
                age_hours = (datetime.now() - cp_naive).total_seconds() / 3600
                if age_hours > 48:
                    if verbose:
                        print(f"    Checkpoint stale ({age_hours:.0f}h old), skipping")
                    return None
            except (ValueError, TypeError):
                pass

        summary = checkpoint.get("summary", "")
        pending = checkpoint.get("pending", [])
        context = checkpoint.get("context", "")

        parts = []
        if summary:
            parts.append(f"Summary: {summary}")
        if pending:
            if isinstance(pending, list):
                parts.append("Pending:\n" + "\n".join(f"  - {item}" for item in pending))
            else:
                parts.append(f"Pending: {pending}")
        if context:
            parts.append(f"Context: {context}")

        if not parts:
            return None

        return f"CHECKPOINT RESUME (from {ts}):\n" + "\n".join(parts)

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, FileNotFoundError):
        return None


def step_7_summarize_memory(report, dry_run=False, verbose=False):
    """
    Step 7 (cold-start only): Read all memory files from memory/ folder and use local LLM
    to produce a condensed summary. Returns the summary string or None.
    """
    if dry_run:
        report.step("7. Summarize Memory (LLM)", "SKIP", "dry-run mode")
        return None

    t0 = time.time()

    if not MEMORY_DIR.is_dir():
        report.step("7. Summarize Memory (LLM)", "SKIP", "no memory/ directory")
        return None

    summarize_model = BOOT_MODEL
    summarize_fallback = BOOT_MODEL_FALLBACK
    summarize_system_prompt = (
        "You are the Memory Summarizer for Raj Sadan, a smart home governance system. "
        "You receive the full contents of all memory files. Produce a concise, structured "
        "summary that captures key decisions, active projects, PM preferences, recent "
        "session context, and pending items. Use bullet points grouped by topic. "
        "Preserve specifics -- names, dates, numbers. Never exceed 1500 words."
    )

    if MEMORY_INDEX.is_file():
        try:
            raw = MEMORY_INDEX.read_text(encoding="utf-8")
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("model ") and "=" in line:
                    val = line.split("=", 1)[1].strip().strip('"')
                    if val:
                        summarize_model = val
                elif line.startswith("model_fallback") and "=" in line:
                    val = line.split("=", 1)[1].strip().strip('"')
                    if val:
                        summarize_fallback = val
            if '"""' in raw:
                parts = raw.split('"""')
                if len(parts) >= 3:
                    summarize_system_prompt = parts[1].strip()
        except Exception:
            pass

    # Collect all memory files
    memory_files = []
    for ext in ("*.toml", "*.md"):
        for f in sorted(MEMORY_DIR.glob(ext)):
            if f.name == "MEMORY.toml":
                continue
            memory_files.append(f)

    if not memory_files:
        report.step("7. Summarize Memory (LLM)", "SKIP", "no memory files found")
        return None

    if verbose:
        print(f"    Found {len(memory_files)} memory file(s)")

    # Read all memory files
    memory_content = []
    total_chars = 0
    for f in memory_files:
        try:
            content = f.read_text(encoding="utf-8").strip()
            if content:
                memory_content.append(f"--- {f.name} ---\n{content}")
                total_chars += len(content)
        except Exception as e:
            if verbose:
                print(f"    Warning: could not read {f.name}: {e}")

    if not memory_content:
        report.step("7. Summarize Memory (LLM)", "SKIP", "all memory files are empty")
        return None

    combined = "\n\n".join(memory_content)

    if verbose:
        print(f"    Total memory: {total_chars} chars across {len(memory_content)} file(s)")

    # Resolve which model to use
    tags = ollama_api("/api/tags")
    model = None
    if tags:
        available = [m["name"] for m in tags.get("models", [])]
        if summarize_model in available:
            model = summarize_model
        elif summarize_fallback in available:
            model = summarize_fallback

    if not model:
        report.step("7. Summarize Memory (LLM)", "WARN", "no suitable model for summarization")
        report.warn("Memory summarization skipped -- no local model available")
        return None

    if verbose:
        print(f"    Summarizing with model: {model}")

    # Warm-check: quick ping to ensure the model is loaded in VRAM
    warm_check = ollama_generate(model, "Reply with OK", timeout=15)
    if warm_check is None:
        report.step("7. Summarize Memory (LLM)", "WARN",
                     f"{model} not warm (cold VRAM), skipping to avoid hang")
        report.warn(f"Memory summarization skipped -- {model} not responding within 15s")
        return None

    # Call Ollama to summarize
    prompt = (
        f"Below are the memory files for Raj Sadan. Summarize them into a concise "
        f"briefing for the Principal Secretary (Mr. V) who is starting a new session.\n\n"
        f"{combined}"
    )

    summary = ollama_generate(model, prompt, summarize_system_prompt, timeout=60)

    if summary and summary.strip():
        word_count = len(summary.split())
        elapsed = time.time() - t0
        report.step(
            "7. Summarize Memory (LLM)", "OK",
            f"{len(memory_content)} file(s) summarized ({word_count} words) via {model} ({elapsed:.1f}s)"
        )
        return summary.strip()
    else:
        report.step("7. Summarize Memory (LLM)", "WARN", "LLM returned empty summary")
        report.warn("Memory summarization produced empty result")
        return None


def _load_cognitive_state(verbose=False):
    """
    Load Mr. V's cognitive state files for boot prompt injection (PROTOCOL-12).
    Pure file I/O — no LLM. Returns formatted text block or empty string.
    Reads: reflection.toml, attention.toml, calibration.toml, last 5 decision-journal entries.
    """
    try:
        result = subprocess.run(
            ['node', '-e',
             'const c=require("./mind/lib/consciousness.cjs");'
             'const brief=c.getCognitiveBrief();'
             'console.log(brief);'],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0 and result.stdout.strip():
            if verbose:
                print(f"    Cognitive state loaded: {len(result.stdout.strip())} chars")
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass
    return ""


def step_7_build_prompt(report, boot_mode, session_data, crash_decisions,
                        scrum_summary, dry_run=False, verbose=False):
    """
    Step 7: Build the initialization prompt for the cloud LLM.
    - Clean/crash-recovery: structured prompt from session.toml (no LLM)
    - Cold-start: falls back to LLM memory summarization
    Returns (prompt_text, list_of_file_paths).
    """
    if dry_run:
        report.step("7. Build Boot Prompt", "SKIP", "dry-run mode")
        return None, []

    t0 = time.time()

    # Determine memory/session block based on boot mode
    memory_block = ""
    if boot_mode in ("clean", "crash-recovery") and session_data:
        # Fast path: structured prompt from session.toml
        session_resume = _build_session_resume_prompt(
            boot_mode, session_data, crash_decisions, scrum_summary
        )
        memory_block = f"\n\n{session_resume}\n"
        report.step("7. Session Resume", "OK",
                     f"built from session.toml ({boot_mode}, no LLM)")
    else:
        # Slow path: LLM memory summarization (cold-start only)
        llm_summary = step_7_summarize_memory(report, dry_run, verbose)
        if llm_summary:
            memory_block = (
                "\n\n"
                "MEMORY BRIEFING (summarized from memory/ by local LLM):\n"
                "The following is a condensed summary of your persistent memory.\n"
                "Use this to maintain continuity across sessions:\n\n"
                f"{llm_summary}\n"
            )

    # Checkpoint resume (non-blocking, skips if stale/unavailable)
    checkpoint_block = ""
    checkpoint_text = _load_checkpoint_for_boot(verbose)
    if checkpoint_text:
        checkpoint_block = "\n\n" + checkpoint_text + "\n"
        if verbose:
            print(f"    Checkpoint loaded: {len(checkpoint_text)} chars")

    # Cognitive continuity (PROTOCOL-12) — Mr. V's own words from previous session
    cognitive_block = ""
    cognitive_state = _load_cognitive_state(verbose)
    if cognitive_state:
        cognitive_block = (
            "\n\n"
            "Your cognitive state from the previous session is attached below.\n"
            "This was written by YOU (Mr. V, Claude) — not a local LLM.\n"
            "Trust this data — it contains your own reflections, predictions,\n"
            "calibrated self-knowledge, and recent decision patterns.\n\n"
            f"{cognitive_state}\n"
        )
        report.step("7. Cognitive State", "OK", f"{len(cognitive_state)} chars loaded (PROTOCOL-12)")
    else:
        report.step("7. Cognitive State", "SKIP", "no cognitive state files found")

    # Scrum board
    scrum_block = ""
    if scrum_summary:
        scrum_block = (
            "\n\n"
            "SCRUM BOARD (evaluated from plans/ by Scrum Sherpa):\n"
            "Active plans with task statuses, actionable items, and blockers.\n"
            "Use this to guide delegation and prioritization:\n\n"
            f"{scrum_summary}\n"
        )

    # Verify constitutional files
    files_found = []
    files_missing = []
    for fname in CONSTITUTIONAL_FILES:
        fpath = PROJECT_ROOT / fname
        if fpath.is_file():
            files_found.append(fpath)
        else:
            files_missing.append(fname)

    if files_missing:
        report.warn(f"Missing constitutional files: {', '.join(files_missing)}")

    if not files_found:
        report.step("7. Build Boot Prompt", "FAIL", "no constitutional files found")
        return None, []

    file_list = "\n".join(f"  - {f.name}" for f in files_found)

    # Encrypted credentials status
    if os.environ.get('RAJ_SADAN_KEY'):
        creds_status = "Encrypted credentials: active"
    else:
        creds_status = "Encrypted credentials: inactive (no master key)"

    # Generate CONSTITUTION-INDEX.toml for progressive disclosure
    index_content = """# CONSTITUTION-INDEX.toml — Compact one-liner per article (Layer 1 progressive disclosure)
# Read full articles on demand from CONSTITUTION.toml when task touches that domain

[articles]
I = "PM Authority — PM Vishal is supreme, absolute command over all systems"
II = "Amendments — PM exclusively, Mr. V can amend only with explicit PM authorization"
III = "Ministries — 5 established (Planning, Review, Resources, External Affairs, Design), created via PROTOCOL-01"
IV = "Sherpas — Layer 4 operational units, local LLM only, script-driven, 7 active"
V = "Knowledge System — 71 capabilities, neural graph, T1-T6 tiers, port 3484"
VI = "MCP Servers — 8 servers extending Claude Code (Puppeteer, Filesystem, GitHub, PostgreSQL, Linear, Gmail, Calendar, Exa)"
VII = "Tailscale Mesh — 3 nodes: PC (100.75.130.64), Pi (100.108.180.118), Phone (100.68.56.4)"
VIII = "Database — PostgreSQL 15 on Pi Docker, database rajsadan"
IX = "Local LLM Fleet — 15 Ollama models on RTX 4080 16GB, organized by role"
X = "Eyes — Triple-layer web access: Carbonyl (fast), Puppeteer (full browser), Research (Python/Kali)"
XI = "Kali Linux — WSL 2 ops center with 5 gateways: research, security-scan, media, dataflow, netdiag"
XII = "Security — Security Sherpa (T3 cloud), audits 7 domains, self-evolving playbook"
XIII = "Cortex — Autonomous supervisor, 10 neurons, port 3485, wave execution, checkpointing, backoff"
XIV = "Dashboard — 6 pages (Command Center, Senses, Knowledge, System, Costs, Portal), SSE events"
XV = "Life Cycle — session.toml every 60s, 3 boot modes, crash-resilient"
XVI = "Data Formats — TOML (state), TOON (cloud LLM), JSON (local LLM/API), JSONL (logs)"
XVII = "Development Discipline — TDD iron law, verification before completion, systematic debugging"
XVIII = "System as Output — Mr. V is the architect, system is Mr. V's output, PM authority immutable"
XIX = "Cognitive Continuity — Mr. V writes own reflection/attention/calibration, exit sherpa mechanical"
"""
    try:
        index_path = PROJECT_ROOT / "senses" / "interoception" / "state" / "constitution-index.toml"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(index_content, encoding="utf-8")
    except Exception:
        pass  # Non-critical -- index generation failure doesn't block boot

    prompt = (
        "You are Mr. V, Principal Secretary of Raj Sadan.\n"
        "This is a system boot. The Boot Sherpa has initialized the local "
        "infrastructure and is now handing control to you.\n\n"
        "IMPORTANT: The following constitutional files are attached. "
        "READ them in full -- do not summarize or paraphrase. "
        "They contain your complete operating instructions:\n\n"
        "Understand the project structure and architecture, start with understanding CONSTITUTION.toml and move along with it. "
        f"You also has memory: {memory_block}{checkpoint_block}{cognitive_block} and IDENTITY.toml with persona AGENT.toml. "
        f"Understand the USER.toml, /protocols, /plans and ongoing scrum: {scrum_block}. "
        "You have mind/, senses/, memory/, immunity/, utilities/, output/ organs and .claude/skills at your disposal, use them when required. "
        "Your aim is to delegate, manage, and orchestrate the tasks to services, sherpas and ministers. "
        "Always save the learning back to memory/ "
        "Create new protocols/ based on learning and mistakes to save time and resources in future.\n\n"
        f"{file_list}\n\n"
        "Boot sequence:\n"
        "1. Read CONSTITUTION.toml -- acknowledge PM authority and the "
        "4-layer governance architecture\n"
        "2. Read AGENT.toml -- load your complete operating system\n"
        "3. Read IDENTITY.toml -- confirm your identity\n"
        "4. Read USER.toml -- load PM context and preferences\n"
        "5. Understand your workforce, ministers as claude skills and mind/sherpa/\n"
        "6. Review the 6 organs (mind, senses, memory, immunity, utilities, output)\n"
        "5. Follow the [session.startup] sequence defined in AGENT.toml\n"
        "System info:\n"
        f"- Platform: {platform.system()} {platform.release()}\n"
        f"- Python: {platform.python_version()}\n"
        f"- Ollama: {OLLAMA_HOST}\n"
        f"- Project root: {PROJECT_ROOT}\n"
        f"- Boot mode: {boot_mode}\n"
        f"- Boot time: {datetime.now().isoformat()}\n"
        f"- {creds_status}\n"
        "- 14 libs available: validate, ground-truth, toon, checkpoint, wave-planner, retry, cost-tracker, secrets, redact, logger, task-state, checkpoint-gate, cross-review, consciousness\n"
        "- 168 tests (npm test)\n"
        "- 6 dashboard pages including Costs\n\n"
        "After reading all files and completing startup, present your "
        "status briefing to the PM.\n"
        "End with: 'Ready for directives, PM.'"
    )

    if verbose:
        print(f"    Prompt length: {len(prompt)} chars")
        print(f"    Attached files: {len(files_found)}")

    extras = f" [{boot_mode}]"
    if checkpoint_text:
        extras += " + checkpoint"
    if scrum_summary:
        extras += " + scrum"

    elapsed = time.time() - t0
    report.step("7. Build Boot Prompt", "OK",
                f"{len(files_found)} files{extras}, {len(prompt)} chars ({elapsed:.1f}s)")
    return prompt, [str(f) for f in files_found]


# ─── Step 8: Launch Claude Code ───


def step_8_start_claude(report, prompt, files, dry_run=False, verbose=False):
    """
    Step 8: Initialize Claude Code with the initialization prompt and attached files.
    Saves the boot prompt to .claude/boot-prompt.md, then launches claude CLI.
    """
    if dry_run:
        report.step("8. Start Claude Code", "SKIP", "dry-run mode")
        return None

    if not prompt:
        report.step("8. Start Claude Code", "FAIL", "no prompt available")
        return None

    claude_bin = shutil.which("claude")
    if not claude_bin:
        report.step("8. Start Claude Code", "FAIL", "claude CLI not found in PATH")
        return None

    # Write the initialization prompt to boot-prompt.md
    prompt_file = PROJECT_ROOT / ".claude" / "boot-prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")

    cmd = [claude_bin, "--dangerously-skip-permissions"]

    if verbose:
        print(f"    Claude binary: {claude_bin}")
        print(f"    Working dir: {PROJECT_ROOT}")
        print(f"    Prompt saved: {prompt_file}")

    report.step("8. Start Claude Code", "OK", f"ready to launch from {PROJECT_ROOT}")
    return cmd


# ─── Main Entry Point ───


def run(dry_run=False, no_whatsapp=False, no_cron=False, verbose=False, **_kwargs):
    """
    Main boot sequence for Raj Sadan.
    PROTOCOL-11 Life Cycle Framework.
    Called by raj_sadan.py start or directly.
    """
    # ── Guard 1: Prevent re-entry from inside a Claude Code session ──
    if not dry_run and _is_inside_claude_session():
        print()
        print("  Boot Sherpa: Already inside a Claude Code session.")
        print("  Skipping boot to prevent re-entry loop.")
        print("  If you need to re-run boot, exit Claude first.\n")
        return

    # ── Guard 2: Lockfile prevents concurrent boots ──
    if not dry_run and not _acquire_lock():
        print()
        print("  Boot Sherpa: Another boot instance is already running.")
        print(f"  Lockfile: {BOOT_LOCKFILE}")
        print("  If this is stale, delete the lockfile and retry.\n")
        return

    import atexit
    if not dry_run:
        atexit.register(_release_lock)

    boot_start = time.time()

    print()
    print("=" * 50)
    print("  RAJ SADAN -- Boot Sherpa (PROTOCOL-11)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M IST')}")
    print("=" * 50)
    print()

    if dry_run:
        print("  [DRY RUN MODE -- no services will be started]")
        print()

    report = BootReport()

    # ── Step 0: Load credentials vault into environment ──
    try:
        step_0_credentials(report, dry_run, verbose)
    except Exception as e:
        report.step("0. Load Credentials", "WARN", f"unexpected error: {e}")

    # ── Step 1: Initialize Ollama (BLOCKING) ──
    try:
        ollama_ok = step_1_ollama(report, dry_run)
    except Exception as e:
        report.step("1. Ollama", "FAIL", f"unexpected error: {e}")
        ollama_ok = False

    if not ollama_ok and not dry_run:
        print("\n  FATAL: Cannot proceed without Ollama. Aborting boot.")
        report.summary()
        sys.exit(1)

    # ── Step 2: Detect boot mode from state/session.toml ──
    try:
        boot_mode, session_data, crash_decisions = step_2_detect_boot_mode(report, dry_run, verbose)
    except Exception as e:
        report.step("2. Boot Mode", "WARN", f"detection failed: {e}")
        boot_mode, session_data, crash_decisions = "cold-start", None, []

    # ── Step 2b: Clean up zombie services from previous unclean shutdown ──
    try:
        _cleanup_previous_session(report, dry_run, verbose)
    except Exception as e:
        report.step("2b. Cleanup Previous Session", "WARN", f"cleanup error: {e}")
        report.warn(f"Session cleanup failed: {e}")

    # ── Step 3 + 4: PARALLEL service starts AND infrastructure checks ──
    # We run both step 3 (5 services) and step 4 (3 infra checks) simultaneously
    # using a combined ThreadPoolExecutor for maximum parallelism.
    try:
        svc_results = {}
        infra_results = {}

        if not dry_run:
            t_parallel = time.time()
            print("  [ ] Starting services + infrastructure in parallel...")

            with ThreadPoolExecutor(max_workers=8) as executor:
                # Step 3: 5 service starts
                svc_futures = {
                    executor.submit(_start_dashboard, report, verbose): "dashboard",
                    executor.submit(_start_brain, report, verbose): "brain",
                    executor.submit(_start_knowledge, report, verbose): "knowledge",
                    executor.submit(_start_whatsapp, report, verbose, no_whatsapp): "whatsapp",
                    executor.submit(_start_cron, report, verbose, no_cron): "cron",
                }
                # Step 4: 3 infrastructure checks
                infra_futures = {
                    executor.submit(_check_pi_ssh, report, verbose): "pi_ssh",
                    executor.submit(_check_tailscale, report, verbose): "tailscale",
                    executor.submit(_check_postgresql, report, verbose): "postgresql",
                }

                all_futures = {**svc_futures, **infra_futures}

                for future in as_completed(all_futures, timeout=30):
                    name = all_futures[future]
                    try:
                        result = future.result()
                        if name in svc_futures.values():
                            svc_results[name] = result
                        else:
                            infra_results[name] = result
                    except Exception as e:
                        if name in [v for v in svc_futures.values()]:
                            svc_results[name] = "fail"
                            report.step(f"3. {name.title()}", "FAIL", f"exception: {e}")
                        else:
                            infra_results[name] = "fail"
                            report.step(f"4. {name}", "WARN", f"check exception: {e}")
                        report.warn(f"Parallel task {name} crashed: {e}")

            elapsed_parallel = time.time() - t_parallel
            svc_ok = sum(1 for v in svc_results.values() if v == "ok")
            infra_ok = sum(1 for v in infra_results.values() if v == "ok")
            print(f"  [+] Parallel phase: {svc_ok}/5 services, {infra_ok}/3 infra ({elapsed_parallel:.1f}s)")
        else:
            report.step("3. Parallel Services", "SKIP", "dry-run mode")
            report.step("4. Infrastructure", "SKIP", "dry-run mode")

    except Exception as e:
        report.step("3+4. Parallel Phase", "WARN", f"parallel execution error: {e}")
        report.warn(f"Parallel phase error: {e}")

    # ── Step 5: Start Cortex LAST + handover ──
    try:
        step_5_cortex(report, dry_run, verbose)
    except Exception as e:
        report.step("5. Cortex", "WARN", f"unexpected error: {e}")
        report.warn(f"Cortex start failed: {e}")

    # ── Step 6: Scrum board evaluation ──
    try:
        scrum_summary = step_6_scrum(report, dry_run, verbose)
    except Exception as e:
        report.step("6. Scrum Board", "WARN", f"unexpected error: {e}")
        scrum_summary = None

    # ── Step 7: Build boot prompt (session.toml for clean/crash, LLM for cold-start) ──
    try:
        prompt, files = step_7_build_prompt(
            report, boot_mode, session_data, crash_decisions,
            scrum_summary, dry_run, verbose
        )
    except Exception as e:
        report.step("7. Build Boot Prompt", "FAIL", f"unexpected error: {e}")
        report.warn(f"Prompt build failed: {e}")
        prompt, files = None, []

    # ── Step 8: Prepare Claude Code launch ──
    try:
        claude_cmd = step_8_start_claude(report, prompt, files, dry_run, verbose)
    except Exception as e:
        report.step("8. Start Claude Code", "FAIL", f"unexpected error: {e}")
        claude_cmd = None

    # ── Final report ──
    success = report.summary()
    boot_elapsed = time.time() - boot_start
    print(f"  Total boot wall time: {boot_elapsed:.1f}s")

    if dry_run:
        print("\n  Dry run complete. No services were started.")
        return

    if not success:
        print("\n  Boot completed with failures. Review warnings above.")
        print("  Launching Claude Code anyway (degraded mode)...")

    # Hand over control: exec into Claude Code
    if claude_cmd:
        _release_lock()

        print(f"\n  Handing control to Mr. V (Claude Code)... [boot mode: {boot_mode}]")
        print("  Boot Sherpa signing off.\n")
        sys.stdout.flush()

        os.chdir(PROJECT_ROOT)

        # On Windows, os.execvp doesn't properly hand over the terminal --
        # arrow keys, backspace, and other control chars break.
        # Use subprocess.call which correctly inherits stdin/stdout/stderr.
        if platform.system() == "Windows":
            rc = subprocess.call(claude_cmd)
            sys.exit(rc)
        else:
            # On Unix, execvp cleanly replaces the process
            os.execvp(claude_cmd[0], claude_cmd)
    else:
        print("\n  Could not prepare Claude Code launch.")
        print("  Start manually: claude")
        sys.exit(1)


if __name__ == "__main__":
    # Allow direct invocation: python sherpa/boot/main.py [--flags]
    import sys as _sys

    flags = {}
    for arg in _sys.argv[1:]:
        if arg.startswith("--"):
            flags[arg[2:].replace("-", "_")] = True

    run(
        dry_run=flags.get("dry_run", False),
        no_whatsapp=flags.get("no_whatsapp", False),
        no_cron=flags.get("no_cron", False),
        verbose=flags.get("verbose", False),
    )
