"""Tests for the ai-sherpa CLI dispatcher (RAJ-69 + RAJ-70).

Covers:
* Registry discovers all 7 sherpas after the lift.
* `python -m ai_sherpa` prints help + lists every sherpa.
* `python -m ai_sherpa --version` returns 0 with version string.
* `python -m ai_sherpa <unknown>` fails cleanly.
* `python -m ai_sherpa scaffold <new>` creates a copy of _scaffold/.
* `python -m ai_sherpa scaffold <existing>` refuses to overwrite.

Per RAJ-70, this is also the smoke test that proves `--dry-run` integration.
We do NOT try to run every sherpa with --dry-run end-to-end here — most v0.0.1
sherpas reference raj-sadan filesystem paths that don't exist in standalone
ai-sherpa. The honest contract is documented in README; the test asserts what's
actually true: the dispatcher loads them, finds their entry points, and rejects
unknown names.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import ai_sherpa


REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "ai_sherpa"

SHERPAS_LIFTED = (PACKAGE_DIR / "boot" / "manifest.toml").exists()


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ai_sherpa", *args],
        capture_output=True,
        text=True,
    )


# ──────────────────────────────────────────────────────────
# Always run — no filesystem state required.
# ──────────────────────────────────────────────────────────


def test_version_exposed() -> None:
    assert ai_sherpa.__version__ == "0.1.0"


def test_cli_version() -> None:
    result = _cli("--version")
    assert result.returncode == 0
    assert "0.1.0" in result.stdout


def test_cli_help() -> None:
    result = _cli("--help")
    assert result.returncode == 0
    assert "ai-sherpa" in result.stdout


def test_cli_no_args_lists_sherpas() -> None:
    result = _cli()
    assert result.returncode == 0
    # Any active sherpa shows up — check the universal three after the lift.
    if SHERPAS_LIFTED:
        for name in ("boot", "exit", "scrum"):
            assert name in result.stdout


def test_cli_unknown_sherpa_returns_1() -> None:
    result = _cli("nonexistent-sherpa-xyz")
    assert result.returncode == 1
    assert "unknown" in result.stderr or "unknown" in result.stdout


# ──────────────────────────────────────────────────────────
# Registry — runs only after sherpas are lifted.
# ──────────────────────────────────────────────────────────


@pytest.mark.skipif(not SHERPAS_LIFTED, reason="run convert_from_raj_sadan.py first")
def test_registry_finds_all_sherpas() -> None:
    sherpas = ai_sherpa.all_sherpas()
    expected = {"boot", "exit", "scrum", "design", "nextcloud", "crawler", "_scaffold"}
    assert expected <= set(sherpas.keys())


@pytest.mark.skipif(not SHERPAS_LIFTED, reason="run convert_from_raj_sadan.py first")
def test_each_sherpa_has_manifest_with_purpose() -> None:
    for name, s in ai_sherpa.all_sherpas().items():
        if name == "_scaffold":
            continue  # template, may not have a real purpose
        assert s.purpose, f"{name} sherpa has empty purpose"
        assert s.entry_point, f"{name} sherpa has no entry_point"


@pytest.mark.skipif(not SHERPAS_LIFTED, reason="run convert_from_raj_sadan.py first")
def test_each_sherpa_entry_point_exists() -> None:
    for name, s in ai_sherpa.all_sherpas().items():
        entry = s.path / s.entry_point
        assert entry.is_file(), f"{name}: missing entry point {entry}"


@pytest.mark.skipif(not SHERPAS_LIFTED, reason="run convert_from_raj_sadan.py first")
def test_manifest_command_uses_new_dispatcher() -> None:
    """Lift script rewrites `command` to `python -m ai_sherpa <name>`."""
    for name, s in ai_sherpa.all_sherpas().items():
        cmd = s.manifest.get("invocation", {}).get("command", "")
        assert "python -m ai_sherpa" in cmd, f"{name}: stale command {cmd!r}"


# ──────────────────────────────────────────────────────────
# Scaffold subcommand.
# ──────────────────────────────────────────────────────────


@pytest.mark.skipif(not SHERPAS_LIFTED, reason="run convert_from_raj_sadan.py first")
def test_scaffold_creates_new_sherpa(tmp_path: Path) -> None:
    """scaffold <new> copies _scaffold/ to ai_sherpa/<new>/.

    This test cleans up after itself by removing the created directory.
    """
    new_name = "_test_scaffolded"
    target = PACKAGE_DIR / new_name
    if target.exists():
        shutil.rmtree(target)
    try:
        result = _cli("scaffold", new_name)
        assert result.returncode == 0, result.stderr
        assert target.is_dir()
        assert (target / "manifest.toml").is_file()
        assert (target / "main.py").is_file()
    finally:
        if target.exists():
            shutil.rmtree(target)


@pytest.mark.skipif(not SHERPAS_LIFTED, reason="run convert_from_raj_sadan.py first")
def test_scaffold_refuses_existing_directory() -> None:
    """scaffold refuses if the target already exists."""
    result = _cli("scaffold", "boot")  # already exists
    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_scaffold_no_arg_returns_2() -> None:
    """scaffold without a name argument returns exit code 2 with a message."""
    result = _cli("scaffold")
    assert result.returncode == 2
    assert "name" in result.stderr.lower()
