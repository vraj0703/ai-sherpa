"""End-to-end test for the onboarding sherpa (RAJ-65).

The acid test for M3: feed the onboarding sherpa raj-sadan's actual answers
(scripted, non-interactive) and verify the rendered output matches what raj-sadan
ships as its in-tree constitutional bundle.

This test depends on:
* `ai-constitution` package importable (pip install or vendor checkout on sys.path)
* The fixture `tests/fixtures/raj-sadan-answers.toml` reflecting raj-sadan's current values

It runs against the FRAMEWORK templates from ai-constitution. It does not need raj-sadan
checked out — it generates a fresh bundle and just checks the shape.

The byte-for-byte diff against raj-sadan's actual tree happens in raj-sadan's
own M6 RAJ-79 round-trip — that test is closer to the consumer's filesystem and
already verified 24/24 reproduce.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "raj-sadan-answers.toml"
WORKBENCH_CONSTITUTION = (HERE.parent.parent / "ai-constitution").resolve()


# Framework checkout might not exist on a clean clone — skip with a clear reason.
pytestmark = pytest.mark.skipif(
    not (WORKBENCH_CONSTITUTION / "ai_constitution" / "__init__.py").is_file(),
    reason=f"ai-constitution checkout not found at {WORKBENCH_CONSTITUTION} — clone it as a sibling repo",
)


def test_e2e_onboarding_produces_raj_sadan_bundle(tmp_path: Path) -> None:
    """Run the onboarding sherpa with raj-sadan's scripted answers; verify output."""
    output_dir = tmp_path / "out"
    config_out = tmp_path / "org-config.toml"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_sherpa",
            "onboarding",
            "run",
            "--answers",
            str(FIXTURE),
            "--output",
            str(output_dir),
            "--config-out",
            str(config_out),
            "--constitution-dir",
            str(WORKBENCH_CONSTITUTION),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"onboarding failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # The org-config.toml exists and contains raj-sadan's values.
    assert config_out.is_file()
    cfg_text = config_out.read_text(encoding="utf-8")
    assert 'name = "Raj Sadan"' in cfg_text
    assert 'name = "Vishal"' in cfg_text
    assert 'name = "Mr. V"' in cfg_text
    assert 'timezone = "Asia/Kolkata"' in cfg_text

    # The bundle was rendered with the right shape.
    expected_files = [
        "CONSTITUTION.toml",
        "AGENT.toml",
        "IDENTITY.toml",
        "PRINCIPAL.toml",
        "CLAUDE.md",
        "boot-prompt.md",
        "protocols/PROTOCOL-00.toml",
        "protocols/PROTOCOL-12.toml",
        "skills/amend/SKILL.md",
        "skills/boot/SKILL.md",
    ]
    for rel in expected_files:
        assert (output_dir / rel).is_file(), f"missing rendered file: {rel}"

    # Substituted content shows up in the rendered files.
    constitution = (output_dir / "CONSTITUTION.toml").read_text(encoding="utf-8")
    assert "Raj Sadan" in constitution
    assert "Mr. V" in constitution
    # No leftover template syntax — substitution actually ran.
    assert "{{" not in constitution
    assert "}}" not in constitution

    # The .onboarding-log.json captures the Q&A.
    log_path = config_out.parent / ".onboarding-log.json"
    assert log_path.is_file()
    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert log["events"]
    keys = {e["key"] for e in log["events"]}
    assert "org.name" in keys
    assert "org.agent.name" in keys


def test_e2e_dry_run_does_not_write(tmp_path: Path) -> None:
    """--dry-run prints the would-be config but writes nothing."""
    output_dir = tmp_path / "out"
    config_out = tmp_path / "org-config.toml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_sherpa",
            "onboarding",
            "run",
            "--answers",
            str(FIXTURE),
            "--output",
            str(output_dir),
            "--config-out",
            str(config_out),
            "--constitution-dir",
            str(WORKBENCH_CONSTITUTION),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr
    assert "Raj Sadan" in result.stdout
    assert not config_out.exists()
    assert not (output_dir / "CONSTITUTION.toml").exists()


def test_e2e_sync_no_changes_renders_idempotently(tmp_path: Path) -> None:
    """First run + a sync against the same config → both write the same bundle."""
    output_dir = tmp_path / "out"
    config_out = tmp_path / "org-config.toml"

    # First, the onboarding produces the bundle.
    r1 = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_sherpa",
            "onboarding",
            "run",
            "--answers",
            str(FIXTURE),
            "--output",
            str(output_dir),
            "--config-out",
            str(config_out),
            "--constitution-dir",
            str(WORKBENCH_CONSTITUTION),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert r1.returncode == 0, r1.stderr
    first_render = (output_dir / "CONSTITUTION.toml").read_bytes()

    # Then the sync — same defaults, no schema drift, should re-render unchanged.
    r2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_sherpa",
            "onboarding",
            "sync",
            "--config",
            str(config_out),
            "--output",
            str(output_dir),
            "--constitution-dir",
            str(WORKBENCH_CONSTITUTION),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert r2.returncode == 0, r2.stderr
    second_render = (output_dir / "CONSTITUTION.toml").read_bytes()
    assert first_render == second_render


def test_e2e_required_field_failure_blocks_render(tmp_path: Path) -> None:
    """If org.name is left as "Your Org", onboarding refuses to proceed."""
    bad_fixture = tmp_path / "bad-answers.toml"
    bad_fixture.write_text(
        '[answers]\n'
        '"organization name" = "Your Org"\n'   # placeholder — must be rejected
        '"your name" = "Vishal"\n'
        '"agent name" = "Friday"\n'
        '"which ministers to enable" = ["planning"]\n'
        '"optional sherpas to enable" = []\n'
        '"which protocols to enable" = ["00", "12"]\n'
        '"ollama host" = "http://localhost:11434"\n'
        '"cloud llm provider" = ""\n'
        '"pick the organs your org runs" = []\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_sherpa",
            "onboarding",
            "run",
            "--answers",
            str(bad_fixture),
            "--output",
            str(tmp_path / "out"),
            "--config-out",
            str(tmp_path / "org-config.toml"),
            "--constitution-dir",
            str(WORKBENCH_CONSTITUTION),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 1
    assert "org.name" in result.stderr
