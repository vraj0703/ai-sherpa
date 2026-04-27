#!/usr/bin/env python3
"""
Scrum Sherpa -- Raj Sadan Plan Evaluation Engine
Authority: CONSTITUTION.toml, Article IV
Protocol: PROTOCOL-04

The Scrum Sherpa evaluates all active plans and produces a scrum board:
 1. Read all plan files from plans/ folder
 2. Evaluate task dependencies and statuses
 3. Identify actionable tasks (resident input first)
 4. Generate scrum summary via local LLM
 5. Send WhatsApp alerts for blockers and PM input
 6. Return summary for boot prompt injection
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ─── Constants ───

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLANS_DIR = PROJECT_ROOT / "plans"

WHATSAPP_HOST = "http://127.0.0.1:3478"


def _normalize_ollama_host(host):
    if not host:
        return "http://localhost:11434"
    if host.startswith("http://") or host.startswith("https://"):
        return host
    host_part = host.split(":")[0]
    if host_part == "0.0.0.0":
        host = host.replace("0.0.0.0", "localhost", 1)
    return f"http://{host}"


OLLAMA_HOST = _normalize_ollama_host(os.environ.get("OLLAMA_HOST", ""))
SCRUM_MODEL = "llama3.2:3b"
SCRUM_MODEL_FALLBACK = "phi3:latest"


# ─── Report Class ───


class ScrumReport:
    """Collects step results for the scrum report."""

    def __init__(self):
        self.steps = []
        self.start_time = time.time()
        self.warnings = []

    def step(self, name, status, detail=""):
        elapsed = time.time() - self.start_time
        self.steps.append(
            {"name": name, "status": status, "detail": detail, "elapsed": f"{elapsed:.1f}s"}
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
        print(f"  Scrum Report: {ok} OK, {warn} WARN, {skip} SKIP, {fail} FAIL")
        print(f"  Total time: {total:.1f}s")
        if self.warnings:
            print(f"  Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                print(f"    ! {w}")
        print("=" * 50)
        return fail == 0


# ─── TOML Parser (minimal) ───


def _parse_plan_toml(path):
    """
    Minimal TOML parser for plan files.
    Handles [meta], [context], and [[tasks]] array of tables.
    Returns dict with 'meta', 'context', 'tasks' keys.
    """
    raw = path.read_text(encoding="utf-8")
    plan = {"meta": {}, "context": {}, "tasks": [], "_path": str(path), "_name": path.stem}

    current_section = None
    current_task = None
    current_key_for_multiline = None
    multiline_value = []

    for line in raw.splitlines():
        stripped = line.strip()

        # Skip comments and empty lines
        if not stripped or stripped.startswith("#"):
            continue

        # Handle [[tasks]] — array of tables
        if stripped == "[[tasks]]":
            if current_task is not None:
                plan["tasks"].append(current_task)
            current_task = {}
            current_section = "task"
            current_key_for_multiline = None
            continue

        # Handle [meta] and [context]
        m = re.match(r"^\[(\w+)\]$", stripped)
        if m:
            if current_task is not None:
                plan["tasks"].append(current_task)
                current_task = None
            current_section = m.group(1)
            current_key_for_multiline = None
            continue

        # Key-value pairs
        if "=" in stripped and current_key_for_multiline is None:
            key, _, val = stripped.partition("=")
            key = key.strip()
            val = val.strip()

            # Parse value
            parsed = _parse_toml_value(val)

            # Multi-line array starting with [
            if val.startswith("[") and not val.endswith("]"):
                current_key_for_multiline = key
                multiline_value = [val]
                continue

            target = current_task if current_section == "task" else plan.get(current_section, {})
            if current_section == "task" and current_task is not None:
                current_task[key] = parsed
            elif current_section in plan:
                plan[current_section][key] = parsed
            continue

        # Continue multi-line array
        if current_key_for_multiline is not None:
            multiline_value.append(stripped)
            if "]" in stripped:
                combined = " ".join(multiline_value)
                parsed = _parse_toml_value(combined)
                if current_section == "task" and current_task is not None:
                    current_task[current_key_for_multiline] = parsed
                elif current_section in plan:
                    plan[current_section][current_key_for_multiline] = parsed
                current_key_for_multiline = None
                multiline_value = []

    # Don't forget the last task
    if current_task is not None:
        plan["tasks"].append(current_task)

    return plan


def _parse_toml_value(val):
    """Parse a TOML value string into a Python type."""
    # String
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]

    # Array
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        # Split by comma, handle quoted strings
        items = []
        for item in re.findall(r'"([^"]*)"', inner):
            items.append(item)
        return items

    # Boolean
    if val == "true":
        return True
    if val == "false":
        return False

    # Integer
    if re.match(r"^\d+$", val):
        return int(val)

    # Float
    if re.match(r"^\d+\.\d+$", val):
        return float(val)

    # Unquoted string (fallback)
    return val


# ─── Utilities ───


def ollama_api(endpoint, method="GET", data=None, timeout=10):
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


def _whatsapp_send_group(group, message):
    url = f"{WHATSAPP_HOST}/send-group"
    req = urllib.request.Request(url, method="POST")
    req.data = json.dumps({"group": group, "message": message}).encode()
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _resolve_model():
    tags = ollama_api("/api/tags")
    if not tags:
        return None
    available = [m["name"] for m in tags.get("models", [])]
    if SCRUM_MODEL in available:
        return SCRUM_MODEL
    if SCRUM_MODEL_FALLBACK in available:
        return SCRUM_MODEL_FALLBACK
    return None


# ─── Plan Analysis ───


def read_all_plans(verbose=False):
    """Read and parse all .toml files in plans/ folder."""
    if not PLANS_DIR.is_dir():
        return []

    plans = []
    for f in sorted(PLANS_DIR.glob("*.toml")):
        try:
            plan = _parse_plan_toml(f)
            plans.append(plan)
            if verbose:
                print(f"    Read: {f.name} ({len(plan['tasks'])} tasks)")
        except Exception as e:
            if verbose:
                print(f"    WARN: Could not parse {f.name}: {e}")

    return plans


def evaluate_dependencies(plan):
    """
    Evaluate task dependencies for a single plan.
    Returns dict with: actionable, blocked, resident_input, completed, pending lists.
    """
    tasks = plan.get("tasks", [])
    task_map = {t["id"]: t for t in tasks if "id" in t}

    result = {
        "actionable": [],
        "blocked": [],
        "resident_input": [],
        "completed": [],
        "in_progress": [],
        "pending": [],
        "skipped": [],
    }

    for task in tasks:
        tid = task.get("id", "?")
        status = task.get("status", "pending")
        deps = task.get("depends_on", [])
        task_type = task.get("type", "delegation")

        if status == "completed":
            result["completed"].append(task)
            continue
        if status == "skipped":
            result["skipped"].append(task)
            continue
        if status == "in_progress":
            result["in_progress"].append(task)
            continue
        if status == "review":
            result["in_progress"].append(task)  # Count review as active
            continue
        if status == "blocked":
            result["blocked"].append(task)
            continue

        # Check if dependencies are met
        deps_met = True
        for dep_id in deps:
            dep_task = task_map.get(dep_id)
            if not dep_task or dep_task.get("status") not in ("completed", "skipped"):
                deps_met = False
                break

        if deps_met:
            # Task is actionable
            if task_type == "resident_input":
                result["resident_input"].append(task)
            else:
                result["actionable"].append(task)
        else:
            result["pending"].append(task)

    return result


def analyze_all_plans(plans):
    """Analyze all plans and return aggregated results."""
    analysis = {
        "plans": [],
        "all_actionable": [],
        "all_blocked": [],
        "all_resident_input": [],
        "total_tasks": 0,
        "total_completed": 0,
    }

    for plan in plans:
        meta = plan.get("meta", {})
        plan_status = meta.get("status", "draft")

        # Only evaluate active plans
        if plan_status != "active":
            continue

        eval_result = evaluate_dependencies(plan)
        total = len(plan.get("tasks", []))
        completed = len(eval_result["completed"]) + len(eval_result["skipped"])

        plan_info = {
            "id": meta.get("id", plan["_name"]),
            "name": meta.get("name", plan["_name"]),
            "priority": meta.get("priority", "medium"),
            "total": total,
            "completed": completed,
            "in_progress": len(eval_result["in_progress"]),
            "actionable": len(eval_result["actionable"]) + len(eval_result["resident_input"]),
            "blocked": len(eval_result["blocked"]),
            "pending": len(eval_result["pending"]),
            "eval": eval_result,
        }

        analysis["plans"].append(plan_info)
        analysis["all_actionable"].extend(
            [(meta.get("name", "?"), t) for t in eval_result["actionable"]]
        )
        analysis["all_blocked"].extend(
            [(meta.get("name", "?"), t) for t in eval_result["blocked"]]
        )
        analysis["all_resident_input"].extend(
            [(meta.get("name", "?"), t) for t in eval_result["resident_input"]]
        )
        analysis["total_tasks"] += total
        analysis["total_completed"] += completed

    return analysis


# ─── Scrum Board Generation ───


def generate_plain_scrum_board(analysis):
    """Generate a plain-text scrum board (no LLM needed)."""
    lines = []
    today = datetime.now().strftime("%Y-%m-%d")
    lines.append(f"SCRUM BOARD — {today}")
    lines.append("=" * 40)

    if not analysis["plans"]:
        lines.append("No active plans.")
        return "\n".join(lines)

    # Plan summaries
    for p in analysis["plans"]:
        pct = int(p["completed"] / p["total"] * 100) if p["total"] > 0 else 0
        lines.append(f"\nPlan: {p['name']} [{p['priority'].upper()}] ({p['completed']}/{p['total']} tasks, {pct}%)")

        ev = p["eval"]
        for t in ev["completed"]:
            lines.append(f"  DONE  {t['id']}: {t['name']} ({t.get('owner', '?')})")
        for t in ev["in_progress"]:
            lines.append(f"  WORK  {t['id']}: {t['name']} ({t.get('owner', '?')})")
        for t in ev["actionable"] + ev["resident_input"]:
            marker = "PM??" if t.get("type") == "resident_input" else "NEXT"
            lines.append(f"  {marker}  {t['id']}: {t['name']} ({t.get('owner', '?')})")
        for t in ev["blocked"]:
            reason = t.get("blocked_reason", "unknown")
            lines.append(f"  STOP  {t['id']}: {t['name']} — {reason}")
        for t in ev["pending"]:
            deps = ", ".join(t.get("depends_on", []))
            lines.append(f"  WAIT  {t['id']}: {t['name']} (needs {deps})")

    # Highlights
    if analysis["all_resident_input"]:
        lines.append("\nNEEDS PM INPUT:")
        for plan_name, t in analysis["all_resident_input"]:
            lines.append(f"  * {plan_name} / {t['id']}: {t['name']}")

    if analysis["all_blocked"]:
        lines.append("\nBLOCKED:")
        for plan_name, t in analysis["all_blocked"]:
            reason = t.get("blocked_reason", "unknown")
            lines.append(f"  * {plan_name} / {t['id']}: {t['name']} — {reason}")

    # Stats
    lines.append(f"\nTotal: {analysis['total_completed']}/{analysis['total_tasks']} tasks across {len(analysis['plans'])} plan(s)")

    # Execution Waves (parallel scheduling)
    try:
        all_tasks = []
        for p in analysis["plans"]:
            ev = p["eval"]
            for t in ev["actionable"] + ev["resident_input"] + ev["pending"] + ev["in_progress"]:
                all_tasks.append({
                    "id": t.get("id", "?"),
                    "name": t.get("name", "?"),
                    "depends_on": t.get("depends_on", []),
                    "status": t.get("status", "pending"),
                    "owner": t.get("owner", "?"),
                })
        if all_tasks:
            tasks_json = json.dumps(all_tasks)
            result = subprocess.run(
                ['node', '-e',
                 'const wp=require("./mind/lib/wave-planner.cjs");'
                 'const tasks=JSON.parse(process.argv[1]);'
                 'console.log(JSON.stringify(wp.buildWaves(tasks)))',
                 tasks_json],
                capture_output=True, text=True, timeout=10,
                cwd=str(PROJECT_ROOT)
            )
            if result.returncode == 0 and result.stdout.strip():
                waves = json.loads(result.stdout.strip())
                lines.append("\nEXECUTION WAVES:")
                for i, wave in enumerate(waves):
                    task_ids = [t.get("id", "?") if isinstance(t, dict) else str(t) for t in wave]
                    lines.append(f"  Wave {i}: {', '.join(task_ids)}")
    except Exception:
        pass  # Skip wave analysis gracefully on any failure

    return "\n".join(lines)


def generate_llm_scrum_board(analysis, model, verbose=False):
    """Use local LLM to generate a polished scrum board summary."""
    plain_board = generate_plain_scrum_board(analysis)

    system_prompt = (
        "You are the Scrum Sherpa for Raj Sadan, a smart home AI governance system. "
        "You receive a raw scrum board and must produce a concise, well-formatted briefing "
        "for the Principal Secretary (Mr. V). Focus on:\n"
        "1. What needs PM input (highest priority)\n"
        "2. What's blocked and why\n"
        "3. What's actionable and who should pick it up\n"
        "4. Overall progress across plans\n\n"
        "Be concise — 200 words max. Use bullet points. Preserve task IDs and owner names."
    )

    prompt = f"Here is the raw scrum board. Produce a clean briefing:\n\n{plain_board}"

    if verbose:
        print(f"    Generating LLM summary via {model}...")

    summary = ollama_generate(model, prompt, system_prompt, timeout=60)

    if summary and summary.strip():
        return summary.strip()

    # Fallback to plain board
    return plain_board


# ─── Scrum Steps ───


def step_1_read_plans(report, dry_run=False, verbose=False):
    """Step 1: Read all plan files from plans/ folder."""
    if dry_run:
        report.step("1. Read Plans", "SKIP", "dry-run mode")
        return []

    if not PLANS_DIR.is_dir():
        report.step("1. Read Plans", "SKIP", "no plans/ directory")
        return []

    plans = read_all_plans(verbose)

    if not plans:
        report.step("1. Read Plans", "SKIP", "no plan files found")
        return []

    active = sum(1 for p in plans if p.get("meta", {}).get("status") == "active")
    total_tasks = sum(len(p.get("tasks", [])) for p in plans)
    report.step("1. Read Plans", "OK", f"{len(plans)} plan(s), {active} active, {total_tasks} tasks")
    return plans


def step_2_evaluate(report, plans, dry_run=False, verbose=False):
    """Step 2: Evaluate dependencies across all plans."""
    if dry_run or not plans:
        if dry_run:
            report.step("2. Evaluate Dependencies", "SKIP", "dry-run mode")
        return None

    analysis = analyze_all_plans(plans)

    if not analysis["plans"]:
        report.step("2. Evaluate Dependencies", "SKIP", "no active plans")
        return None

    actionable = len(analysis["all_actionable"]) + len(analysis["all_resident_input"])
    blocked = len(analysis["all_blocked"])
    report.step(
        "2. Evaluate Dependencies", "OK",
        f"{analysis['total_completed']}/{analysis['total_tasks']} done, "
        f"{actionable} actionable, {blocked} blocked"
    )
    return analysis


def step_3_identify(report, analysis, dry_run=False, verbose=False):
    """Step 3: Identify actionable tasks and PM input needs."""
    if dry_run or not analysis:
        if dry_run:
            report.step("3. Identify Actionable", "SKIP", "dry-run mode")
        return

    pm_input = len(analysis["all_resident_input"])
    actionable = len(analysis["all_actionable"])

    parts = []
    if pm_input:
        parts.append(f"{pm_input} need PM input")
    if actionable:
        parts.append(f"{actionable} ready for delegation")

    if parts:
        report.step("3. Identify Actionable", "OK", ", ".join(parts))
    else:
        report.step("3. Identify Actionable", "OK", "no actionable tasks right now")

    if verbose:
        if analysis["all_resident_input"]:
            print("    PM INPUT NEEDED:")
            for plan_name, t in analysis["all_resident_input"]:
                print(f"      * {plan_name} / {t['id']}: {t['name']}")
        if analysis["all_actionable"]:
            print("    ACTIONABLE:")
            for plan_name, t in analysis["all_actionable"]:
                print(f"      * {plan_name} / {t['id']}: {t['name']} -> {t.get('owner', '?')}")


def step_4_generate_summary(report, analysis, dry_run=False, verbose=False):
    """Step 4: Generate scrum board summary."""
    if dry_run or not analysis:
        if dry_run:
            report.step("4. Generate Scrum Summary", "SKIP", "dry-run mode")
        return None

    model = _resolve_model()

    if model:
        summary = generate_llm_scrum_board(analysis, model, verbose)
        word_count = len(summary.split())
        report.step("4. Generate Scrum Summary", "OK", f"{word_count} words via {model}")
    else:
        summary = generate_plain_scrum_board(analysis)
        word_count = len(summary.split())
        report.step("4. Generate Scrum Summary", "OK", f"{word_count} words (plain text, no LLM)")

    return summary


def step_5_whatsapp(report, analysis, dry_run=False, no_whatsapp=False, verbose=False):
    """Step 5: Send WhatsApp alerts for blockers and PM input."""
    if dry_run or no_whatsapp or not analysis:
        if dry_run:
            report.step("5. WhatsApp Alerts", "SKIP", "dry-run mode")
        elif no_whatsapp:
            report.step("5. WhatsApp Alerts", "SKIP", "disabled via --no-whatsapp")
        return

    alerts_sent = 0

    # Alert for PM input needed
    if analysis["all_resident_input"]:
        items = []
        for plan_name, t in analysis["all_resident_input"]:
            items.append(f"- {plan_name} / {t['id']}: {t['name']}")
        msg = "PM INPUT NEEDED:\n" + "\n".join(items)
        result = _whatsapp_send_group("general", msg)
        if result and result.get("ok"):
            alerts_sent += 1
        if verbose:
            print(f"    Sent PM input alert to general group")

    # Alert for blocked tasks
    if analysis["all_blocked"]:
        items = []
        for plan_name, t in analysis["all_blocked"]:
            reason = t.get("blocked_reason", "unknown")
            items.append(f"- {plan_name} / {t['id']}: {t['name']} — {reason}")
        msg = "BLOCKED TASKS:\n" + "\n".join(items)
        result = _whatsapp_send_group("alerts-reports", msg)
        if result and result.get("ok"):
            alerts_sent += 1
        if verbose:
            print(f"    Sent blocker alert to alerts-reports group")

    if alerts_sent > 0:
        report.step("5. WhatsApp Alerts", "OK", f"{alerts_sent} alert(s) sent")
    else:
        report.step("5. WhatsApp Alerts", "OK", "no alerts needed")


# ─── Main Entry Point ───


def run(dry_run=False, no_whatsapp=False, verbose=False, **_kwargs):
    """
    Main scrum cycle for Raj Sadan.
    Called by raj_sadan.py plan scrum, boot step, or cron job.
    Returns the scrum summary string (for boot prompt injection).
    """
    print()
    print("=" * 50)
    print("  RAJ SADAN -- Scrum Sherpa")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M IST')}")
    print("=" * 50)
    print()

    if dry_run:
        print("  [DRY RUN MODE -- no WhatsApp alerts will be sent]")
        print()

    report = ScrumReport()

    # Step 1: Read plans
    plans = step_1_read_plans(report, dry_run, verbose)

    # Step 2: Evaluate dependencies
    analysis = step_2_evaluate(report, plans, dry_run, verbose)

    # Step 3: Identify actionable
    step_3_identify(report, analysis, dry_run, verbose)

    # Step 4: Generate scrum summary
    scrum_summary = step_4_generate_summary(report, analysis, dry_run, verbose)

    # Step 5: WhatsApp alerts
    step_5_whatsapp(report, analysis, dry_run, no_whatsapp, verbose)

    # Report
    report.summary()

    if scrum_summary and verbose:
        print("\n--- SCRUM BOARD ---")
        print(scrum_summary)
        print("--- END SCRUM BOARD ---\n")

    return scrum_summary


# Also support being called from boot sherpa directly (returns summary only, quieter)
def evaluate_for_boot(verbose=False):
    """
    Lightweight scrum evaluation for boot step.
    Returns (summary_string, plan_count, actionable_count) or (None, 0, 0).
    """
    if not PLANS_DIR.is_dir():
        return None, 0, 0

    plans = read_all_plans(verbose)
    if not plans:
        return None, 0, 0

    analysis = analyze_all_plans(plans)
    if not analysis["plans"]:
        return None, 0, 0

    # Generate plain text board (fast, no LLM needed for boot)
    summary = generate_plain_scrum_board(analysis)
    actionable = len(analysis["all_actionable"]) + len(analysis["all_resident_input"])

    return summary, len(analysis["plans"]), actionable


if __name__ == "__main__":
    import sys as _sys

    flags = {}
    for arg in _sys.argv[1:]:
        if arg.startswith("--"):
            flags[arg[2:].replace("-", "_")] = True

    run(
        dry_run=flags.get("dry_run", False),
        no_whatsapp=flags.get("no_whatsapp", False),
        verbose=flags.get("verbose", False),
    )
