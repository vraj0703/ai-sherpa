#!/usr/bin/env python3
"""
Exit Sherpa -- Raj Sadan Graceful Shutdown (Mechanical Only)
Authority: CONSTITUTION.toml, Article IV
Protocol: PROTOCOL-11 Life Cycle Framework + PROTOCOL-12 Cognitive Continuity

The Exit Sherpa is PURELY MECHANICAL. It stops services, saves files, verifies.
All cognitive work (reflection, attention, calibration) is done by Mr. V (Claude)
BEFORE invoking this sherpa. See PROTOCOL-12.

Exit sequence:
 0. Set session.toml clean_shutdown = true (crash-safe flag)
 1. Stop Cortex FIRST (prevents restart loops)
 2. Send WhatsApp shutdown notification (best-effort)
 3. Stop all remaining services IN PARALLEL
 4. Save checkpoint to NextCloud (best-effort)
 5. Verify all services stopped
 6. Verify cognitive state files exist (PROTOCOL-12 validation)
 7. Exit report
"""

import concurrent.futures
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ─── Constants ───

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_TOML = os.path.join(PROJECT_ROOT, "senses", "interoception", "state", "session.toml")
COGNITIVE_STATE_DIR = PROJECT_ROOT / "memory" / "journal"

WHATSAPP_HOST = "http://127.0.0.1:3478"
CRON_HOST = "http://127.0.0.1:3479"
CORTEX_PORT = 3485
CORTEX_HOST = "http://127.0.0.1:3485"

SERVICES_TO_STOP = [
    ("Senses", 3483),
    ("Knowledge", 3484),
    ("Dashboard", 3482),
    ("Cron", 3479),
    ("WhatsApp", 3478),
]

ALL_SERVICE_PORTS = [
    ("Cortex", 3485),
    ("Senses", 3483),
    ("Knowledge", 3484),
    ("Dashboard", 3482),
    ("Cron", 3479),
    ("WhatsApp", 3478),
    # Content Engine (3480) purged -- config retained but code deleted
]


def _normalize_ollama_host(host):
    """Normalize OLLAMA_HOST -- add http:// if missing, default to localhost."""
    if not host:
        return "http://localhost:11434"
    if host.startswith("http://") or host.startswith("https://"):
        return host
    host_part = host.split(":")[0]
    if host_part == "0.0.0.0":
        host = host.replace("0.0.0.0", "localhost", 1)
    return f"http://{host}"


OLLAMA_HOST = _normalize_ollama_host(os.environ.get("OLLAMA_HOST", ""))


# ─── Utilities ───


class ExitReport:
    """Collects step results for the final exit report."""

    def __init__(self):
        self.steps = []
        self.start_time = time.time()
        self.warnings = []

    def step(self, name, status, detail=""):
        elapsed = time.time() - self.start_time
        self.steps.append(
            {
                "name": name,
                "status": status,
                "detail": detail,
                "elapsed": f"{elapsed:.1f}s",
            }
        )
        icon = {"OK": "+", "SKIP": "~", "WARN": "!", "FAIL": "x"}[status]
        print(f"  [{icon}] {name}", end="")
        if detail:
            print(f" -- {detail}")
        else:
            print()

    def warn(self, msg):
        self.warnings.append(msg)

    def summary(self):
        total = time.time() - self.start_time
        ok = sum(1 for s in self.steps if s["status"] == "OK")
        skip = sum(1 for s in self.steps if s["status"] == "SKIP")
        warn = sum(1 for s in self.steps if s["status"] == "WARN")
        fail = sum(1 for s in self.steps if s["status"] == "FAIL")

        print()
        print("=" * 50)
        print(f"  Exit Report: {ok} OK, {warn} WARN, {skip} SKIP, {fail} FAIL")
        print(f"  Total time: {total:.1f}s")
        if self.warnings:
            print(f"  Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                print(f"    ! {w}")
        print("=" * 50)

        return fail == 0


def _service_health(host, timeout=3):
    """Generic HTTP health check. Returns parsed JSON or None."""
    url = f"{host}/health"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _whatsapp_send_group(group, message, timeout=5):
    """Send a message to a WhatsApp group by key."""
    url = f"{WHATSAPP_HOST}/send-group"
    req = urllib.request.Request(url, method="POST")
    req.data = json.dumps({"group": group, "message": message}).encode()
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _ollama_health(timeout=2):
    """Quick Ollama health check for verify step. Returns True if running."""
    url = f"{OLLAMA_HOST}/api/tags"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _find_service_pid(port):
    """Find the PID of a process listening on the given port. Returns PID or None."""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                # May return multiple PIDs; take the first
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


def _kill_process(pid, verbose=False):
    """Kill a process by PID. Returns True if successful."""
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["powershell", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                # Fallback to taskkill
                subprocess.run(
                    ["powershell", "-Command", f"taskkill /F /PID {pid} /T"],
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


def _stop_service_by_port(name, port, verbose=False):
    """Stop a service by finding its PID on the given port.
    Returns (name, status, detail) tuple for the caller to report."""
    host = f"http://127.0.0.1:{port}"
    health = _service_health(host, timeout=2)
    if not health:
        return (name, "SKIP", "not running")

    pid = _find_service_pid(port)
    if not pid:
        return (name, "WARN", "responding but PID not found")

    killed = _kill_process(pid, verbose)
    if killed:
        # Brief wait then verify
        time.sleep(1)
        health = _service_health(host, timeout=2)
        if health:
            return (name, "WARN", f"PID {pid} killed but still responding")
        else:
            return (name, "OK", f"stopped (PID {pid})")
    else:
        return (name, "WARN", f"could not kill PID {pid}")


# ─── Exit Steps ───


def _read_session_toml(path):
    """Read session TOML file and return dict."""
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _write_session_toml(path, data):
    """Write session data as TOML. Simple serializer for the fixed session schema."""
    def _toml_val(v):
        if v is None:
            return '""'
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if isinstance(v, list):
            return "[" + ", ".join(_toml_val(i) for i in v) + "]"
        return '"' + str(v) + '"'

    lines = ["# Raj Sadan — Session State"]
    # Top-level scalars
    for key in ("version", "session_id", "started_at", "updated_at",
                "clean_shutdown", "uptime_minutes", "alerts", "memo",
                "shutdown_initiated"):
        if key in data:
            lines.append(f"{key} = {_toml_val(data[key])}")
    lines.append("")

    # Services
    for name, info in data.get("services", {}).items():
        if isinstance(info, dict):
            lines.append(f"[services.{name}]")
            for k, v in info.items():
                lines.append(f"{k} = {_toml_val(v)}")
            lines.append("")

    # Cortex
    cortex = data.get("cortex")
    if cortex and isinstance(cortex, dict):
        lines.append("[cortex]")
        for k in ("loop_count", "decisions_today", "paused", "strategies_count"):
            if k in cortex:
                lines.append(f"{k} = {_toml_val(cortex[k])}")
        lines.append("")
        by_type = cortex.get("by_type", {})
        if by_type:
            lines.append("[cortex.by_type]")
            for k, v in by_type.items():
                lines.append(f"{k} = {_toml_val(v)}")
            lines.append("")

    # Plans
    for plan_id, pinfo in data.get("plans", {}).items():
        if isinstance(pinfo, dict):
            safe_id = plan_id.replace(" ", "_")
            lines.append(f"[plans.{safe_id}]")
            for k, v in pinfo.items():
                lines.append(f"{k} = {_toml_val(v)}")
            lines.append("")

    # Resources
    resources = data.get("resources")
    if resources and isinstance(resources, dict):
        lines.append("[resources]")
        for k, v in resources.items():
            lines.append(f"{k} = {_toml_val(v)}")
        lines.append("")

    # Phone
    phone = data.get("phone")
    if phone and isinstance(phone, dict):
        lines.append("[phone]")
        for k, v in phone.items():
            lines.append(f"{k} = {_toml_val(v)}")
        lines.append("")

    # Recent decisions
    for d in data.get("recent_decisions", []):
        if isinstance(d, dict):
            lines.append("[[recent_decisions]]")
            for k, v in d.items():
                lines.append(f"{k} = {_toml_val(v)}")
            lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def step_0_session_flag(report, dry_run=False, verbose=False):
    """
    Step 0: Set clean_shutdown = true in session.toml.
    This is the FIRST thing that happens -- if power cuts mid-exit,
    at least this flag marks the shutdown was initiated.
    Must complete in <100ms.
    """
    if dry_run:
        report.step("0. Session Flag", "SKIP", "dry-run mode")
        return True

    try:
        # Read existing session.toml or start fresh
        session_data = {}
        if os.path.isfile(SESSION_TOML):
            session_data = _read_session_toml(SESSION_TOML)

        # Set the flag and timestamp
        session_data["clean_shutdown"] = True
        session_data["shutdown_initiated"] = datetime.now().isoformat()

        # Ensure state/ directory exists
        os.makedirs(os.path.dirname(SESSION_TOML), exist_ok=True)

        _write_session_toml(SESSION_TOML, session_data)

        report.step("0. Session Flag", "OK", "clean_shutdown = true")
    except Exception as e:
        report.step("0. Session Flag", "FAIL", f"could not write session.toml: {e}")
        report.warn(f"Session flag failed: {e}")

    return True


def step_1_stop_cortex(report, dry_run=False, verbose=False):
    """
    Step 1: Stop Cortex FIRST to prevent restart loops during shutdown.
    Graceful POST /shutdown with 5s timeout, then PID kill as fallback.
    """
    if dry_run:
        report.step("1. Stop Cortex", "SKIP", "dry-run mode")
        return True

    # Try graceful shutdown via API first
    try:
        req = urllib.request.Request(
            f"{CORTEX_HOST}/shutdown",
            data=b'{}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        report.step("1. Stop Cortex", "OK", "graceful shutdown via API")
        return True
    except Exception:
        pass

    # Fallback: find and kill by port
    pid = _find_service_pid(CORTEX_PORT)
    if pid:
        _kill_process(pid, verbose)
        report.step("1. Stop Cortex", "OK", f"killed PID {pid}")
    else:
        report.step("1. Stop Cortex", "OK", "not running")
    return True


def step_2_notify_shutdown(report, dry_run=False, no_whatsapp=False, verbose=False):
    """
    Step 2: Send shutdown notification to WhatsApp general group.
    Best-effort, 5s timeout. No sleep after sending.
    Includes session cost summary if available.
    """
    if dry_run or no_whatsapp:
        reason = "dry-run mode" if dry_run else "disabled via --no-whatsapp"
        report.step("2. Shutdown Notification", "SKIP", reason)
        return True

    # Gather session cost summary (best-effort)
    cost_data = {"total": 0, "cloud_tokens": 0, "models": 0}
    try:
        cost_result = subprocess.run(
            ['node', '-e', 'const c=require("./lib/cost-tracker.cjs");const s=c.getSummary({since:new Date(Date.now()-86400000)});console.log(JSON.stringify({total:s.total_cost_usd,cloud_tokens:s.total_tokens_in+s.total_tokens_out,models:Object.keys(s.by_model||{}).length}))'],
            capture_output=True, text=True, timeout=5, cwd=str(PROJECT_ROOT)
        )
        if cost_result.returncode == 0:
            cost_data = json.loads(cost_result.stdout.strip())
    except Exception:
        pass

    # Write cost data to session.toml memo for persistence
    if cost_data.get("total", 0) > 0:
        try:
            session_data = {}
            if os.path.isfile(SESSION_TOML):
                session_data = _read_session_toml(SESSION_TOML)
            existing_memo = session_data.get("memo", "")
            cost_memo = f"Session cost: ${cost_data['total']:.4f} ({cost_data['cloud_tokens']} tokens, {cost_data['models']} models)"
            session_data["memo"] = f"{existing_memo}; {cost_memo}" if existing_memo else cost_memo
            _write_session_toml(SESSION_TOML, session_data)
        except Exception:
            pass

    try:
        # Quick health check -- don't waste time if WhatsApp is down
        health = _service_health(WHATSAPP_HOST, timeout=2)
        if not health or health.get("status") != "connected":
            report.step("2. Shutdown Notification", "SKIP", "WhatsApp not connected")
            return True

        timestamp = datetime.now().strftime("%H:%M IST")
        cost_str = ""
        if cost_data.get("total", 0) > 0:
            cost_str = f" Cost: ${cost_data['total']:.4f} ({cost_data['cloud_tokens']} tokens, {cost_data['models']} models)."
        message = f"Raj Sadan shutting down. Gateway closing.{cost_str} ({timestamp})"

        if verbose:
            print("    Sending shutdown notification to general group...")

        result = _whatsapp_send_group("general", message, timeout=5)

        if result and result.get("ok"):
            report.step("2. Shutdown Notification", "OK", "sent to general group")
        else:
            report.step("2. Shutdown Notification", "WARN", "failed to send notification")
            report.warn("Shutdown notification could not be delivered to WhatsApp")
    except Exception as e:
        report.step("2. Shutdown Notification", "WARN", f"error: {e}")

    return True


def step_3_stop_services_parallel(report, dry_run=False, verbose=False):
    """
    Step 3: Stop all remaining services IN PARALLEL using ThreadPoolExecutor.
    Services: Senses(3483), Knowledge(3484), Dashboard(3482), Cron(3479), WhatsApp(3478).
    Each gets its own thread. 10s total timeout.
    """
    if dry_run:
        for name, _ in SERVICES_TO_STOP:
            report.step(f"3. Stop {name}", "SKIP", "dry-run mode")
        return True

    try:
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(SERVICES_TO_STOP)) as executor:
            futures = {
                executor.submit(_stop_service_by_port, name, port, verbose): name
                for name, port in SERVICES_TO_STOP
            }
            # Wait up to 10s for all to complete
            done, not_done = concurrent.futures.wait(futures, timeout=10)

            for future in done:
                try:
                    name, status, detail = future.result()
                    report.step(f"3. Stop {name}", status, detail)
                except Exception as e:
                    svc_name = futures[future]
                    report.step(f"3. Stop {svc_name}", "WARN", f"error: {e}")

            for future in not_done:
                svc_name = futures[future]
                report.step(f"3. Stop {svc_name}", "WARN", "timed out (10s)")
                report.warn(f"Stopping {svc_name} timed out")
                future.cancel()
    except Exception as e:
        report.step("3. Stop Services", "WARN", f"parallel stop error: {e}")
        report.warn(f"Parallel service stop failed: {e}")

    return True


def step_4_save_checkpoint(report, dry_run=False, verbose=False):
    """
    Step 4: Save checkpoint to NextCloud + local state.
    Best-effort, 5s timeout. Non-blocking.
    Cleans up orphaned checkpoints before saving the final one.
    """
    if dry_run:
        report.step("4. Save Checkpoint", "SKIP", "dry-run mode")
        return True

    # Clean up orphaned checkpoints before saving the final one
    try:
        subprocess.run(
            ['node', '-e', 'const cp=require("./lib/checkpoint.cjs");const stale=cp.listStale(0);stale.forEach(s=>cp.clear(s.taskId));console.log("Cleared "+stale.length+" checkpoints")'],
            capture_output=True, text=True, timeout=5, cwd=str(PROJECT_ROOT)
        )
    except Exception:
        pass

    summary = "Session ended"
    gateway_script = PROJECT_ROOT / "senses" / "gateway" / "checkpoint.cjs"

    if not gateway_script.is_file():
        report.step("4. Save Checkpoint", "SKIP", "gateway/checkpoint.cjs not found")
        return True

    try:
        result = subprocess.run(
            ["node", str(gateway_script), "--save", "--summary", summary],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            report.step("4. Save Checkpoint", "OK", "checkpoint saved")
        else:
            stderr = result.stderr.strip()[:120] if result.stderr else "unknown error"
            report.step("4. Save Checkpoint", "WARN", f"save failed: {stderr}")
            report.warn(f"Checkpoint save returned non-zero: {stderr}")
    except subprocess.TimeoutExpired:
        report.step("4. Save Checkpoint", "WARN", "timed out (5s)")
        report.warn("Checkpoint save timed out")
    except (OSError, FileNotFoundError) as e:
        report.step("4. Save Checkpoint", "WARN", f"could not run: {e}")
        report.warn(f"Checkpoint save failed: {e}")

    return True


def step_5_verify(report, dry_run=False, verbose=False):
    """
    Step 5: Verify all services are stopped.
    Parallel health checks on all 6 service ports, 5s timeout.
    """
    if dry_run:
        report.step("5. Verify Shutdown", "SKIP", "dry-run mode")
        return True

    try:
        still_running = []

        def check_service(name, port):
            host = f"http://127.0.0.1:{port}"
            health = _service_health(host, timeout=2)
            if health:
                return name
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(ALL_SERVICE_PORTS)) as executor:
            futures = {
                executor.submit(check_service, name, port): name
                for name, port in ALL_SERVICE_PORTS
            }
            done, _ = concurrent.futures.wait(futures, timeout=5)

            for future in done:
                try:
                    result = future.result()
                    if result:
                        still_running.append(result)
                except Exception:
                    pass

        # Note Ollama state (shared resource, not stopped by us)
        ollama_state = ""
        ollama_up = _ollama_health(timeout=2)
        if ollama_up:
            ollama_state = " (Ollama still running -- shared resource, not stopped)"

        if still_running:
            report.step("5. Verify Shutdown", "WARN",
                        f"still running: {', '.join(still_running)}{ollama_state}")
            report.warn(f"Services still running after shutdown: {', '.join(still_running)}")
        else:
            report.step("5. Verify Shutdown", "OK",
                        f"all services stopped{ollama_state}")
    except Exception as e:
        report.step("5. Verify Shutdown", "WARN", f"verification error: {e}")

    return True


def step_6_verify_cognitive_state(report, dry_run=False, verbose=False):
    """
    Step 6: Verify cognitive state files exist (PROTOCOL-12 validation).
    Mr. V should have written reflection.toml and attention.toml BEFORE
    invoking this sherpa. This step validates, not creates.
    Missing files are WARN, never FAIL — mechanical exit must always complete.
    """
    if dry_run:
        report.step("6. Cognitive State", "SKIP", "dry-run mode")
        return True

    cognitive_files = {
        "reflection.toml": COGNITIVE_STATE_DIR / "reflection.toml",
        "attention.toml": COGNITIVE_STATE_DIR / "attention.toml",
    }

    missing = []
    stale = []
    found = []
    now = time.time()

    for name, filepath in cognitive_files.items():
        if not filepath.is_file():
            missing.append(name)
        else:
            # Check if written recently (within 30 minutes)
            age_minutes = (now - filepath.stat().st_mtime) / 60
            if age_minutes > 30:
                stale.append(f"{name} ({age_minutes:.0f}m old)")
            else:
                found.append(name)

    if missing:
        report.step("6. Cognitive State", "WARN",
                     f"missing: {', '.join(missing)} -- Mr. V should write these before exit (PROTOCOL-12)")
        report.warn(f"Cognitive state missing: {', '.join(missing)}")
    elif stale:
        report.step("6. Cognitive State", "WARN",
                     f"stale: {', '.join(stale)} -- may be from a previous session")
    else:
        report.step("6. Cognitive State", "OK",
                     f"verified: {', '.join(found)}")

    return True


# ─── Main Entry Point ───


def run(dry_run=False, no_whatsapp=False, verbose=False, **_kwargs):
    """
    Main exit sequence for Raj Sadan.
    PROTOCOL-11 Life Cycle Framework + PROTOCOL-12 Cognitive Continuity.
    Called by raj_sadan.py stop or directly.

    IMPORTANT: Mr. V must write cognitive state (reflection, attention,
    calibration) BEFORE invoking this sherpa. This sherpa is purely mechanical.
    """
    print()
    print("=" * 50)
    print("  RAJ SADAN -- Exit Sherpa (Mechanical)")
    print(f"  PROTOCOL-11 + PROTOCOL-12")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M IST')}")
    print("=" * 50)
    print()

    if dry_run:
        print("  [DRY RUN MODE -- no services will be stopped]")
        print()

    report = ExitReport()

    # Step 0: Set clean_shutdown flag IMMEDIATELY (<100ms)
    step_0_session_flag(report, dry_run, verbose)

    # Step 1: Stop Cortex FIRST (prevents restart loops)
    step_1_stop_cortex(report, dry_run, verbose)

    # Step 2: Send WhatsApp notification (best-effort, 5s timeout)
    step_2_notify_shutdown(report, dry_run, no_whatsapp, verbose)

    # Step 3: Stop all remaining services IN PARALLEL (10s)
    step_3_stop_services_parallel(report, dry_run, verbose)

    # Step 4: Save checkpoint to NextCloud (best-effort, 5s)
    step_4_save_checkpoint(report, dry_run, verbose)

    # Step 5: Verify all stopped (parallel health checks, 5s)
    step_5_verify(report, dry_run, verbose)

    # Step 6: Verify cognitive state files (PROTOCOL-12 validation)
    step_6_verify_cognitive_state(report, dry_run, verbose)

    # Step 7: Exit report
    success = report.summary()

    if dry_run:
        print("\n  Dry run complete. No services were stopped.")
    elif success:
        print("\n  Raj Sadan is offline. Goodnight.")
    else:
        print("\n  Shutdown completed with warnings. Review above.")

    print()
    return success


if __name__ == "__main__":
    import sys as _sys

    flags = {}
    i = 1
    while i < len(_sys.argv):
        arg = _sys.argv[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            if i + 1 < len(_sys.argv) and not _sys.argv[i + 1].startswith("--"):
                flags[key] = _sys.argv[i + 1]
                i += 2
            else:
                flags[key] = True
                i += 1
        else:
            i += 1

    run(
        dry_run=flags.get("dry_run", False),
        no_whatsapp=flags.get("no_whatsapp", False),
        verbose=flags.get("verbose", False),
    )
