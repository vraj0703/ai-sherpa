"""Tests for ai_sherpa.onboarding.flow (RAJ-61, RAJ-63)."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout

from ai_sherpa.onboarding.flow import OPTIONAL_OPT_INS, ScriptedPrompter, run_flow


_DEFAULTS = {
    "org": {
        "name": "Your Org",
        "slug": "",
        "tagline": "",
        "timezone": "UTC",
        "pm": {
            "name": "",
            "title": "Prime Minister",
            "pronouns": "they/them",
            "email": "",
        },
        "agent": {
            "name": "Secretary",
            "title": "Principal Secretary",
            "emoji": "🏛️",
            "persona": {"tone": "Sharp, composed, efficient", "keywords": ["architect", "orchestrator"]},
        },
        "ministries": {"enabled": ["planning", "design", "external_affairs", "resources", "review"]},
        "sherpas": {"enabled": ["boot", "exit", "onboarding"]},
        "protocols": {"enabled": ["00", "12"]},
        "llm": {"local": {"host": "http://localhost:11434"}, "cloud": {"provider": ""}},
        "services": {},
    },
}


def test_scripted_run_basic() -> None:
    """A fully-scripted run produces a complete answers dict."""
    answers_dict = {
        "organization name": "Acme",
        "slug": "acme",
        "tagline": "Building boring infra",
        "timezone": "Europe/Madrid",
        "your name": "Vishal",
        "your title": "Founder",
        "your pronouns": "he/him",
        "email": "",
        "agent name": "Friday",
        "agent title": "Principal Secretary",
        "agent emoji": "🤖",
        "persona tone keywords": "Sharp, composed, efficient",
        "persona keywords": "architect, orchestrator",
        "which ministers to enable": ["planning", "review"],
        "optional sherpas to enable": ["scrum"],
        "which protocols to enable": ["00", "12"],
        "ollama host": "http://localhost:11434",
        "cloud llm provider": "",
        "pick the organs your org runs": [],
    }
    p = ScriptedPrompter(answers_dict)
    with redirect_stdout(io.StringIO()):
        result = run_flow(p, _DEFAULTS)
    assert result.org["org"]["name"] == "Acme"
    assert result.org["org"]["agent"]["name"] == "Friday"
    assert result.org["org"]["agent"]["emoji"] == "🤖"
    # Mandatory sherpas always present.
    assert "boot" in result.org["org"]["sherpas"]["enabled"]
    assert "exit" in result.org["org"]["sherpas"]["enabled"]
    assert "onboarding" in result.org["org"]["sherpas"]["enabled"]
    # Optional adds layered on top.
    assert "scrum" in result.org["org"]["sherpas"]["enabled"]
    # Ministries respected.
    assert result.org["org"]["ministries"]["enabled"] == ["planning", "review"]


def test_scripted_run_with_opt_ins() -> None:
    """Selecting optional opt-ins triggers per-organ host prompts."""
    answers_dict = {
        "organization name": "Acme",
        "slug": "acme",
        "tagline": "",
        "timezone": "UTC",
        "your name": "Vishal",
        "your title": "Founder",
        "your pronouns": "he/him",
        "email": "",
        "agent name": "Friday",
        "agent title": "Principal Secretary",
        "agent emoji": "🤖",
        "persona tone keywords": "Sharp, composed, efficient",
        "persona keywords": "architect, orchestrator",
        "which ministers to enable": ["planning", "design", "external_affairs", "resources", "review"],
        "optional sherpas to enable": [],
        "which protocols to enable": ["00", "12"],
        "ollama host": "http://localhost:11434",
        "cloud llm provider": "",
        "pick the organs your org runs": ["mind", "knowledge"],
        "where does your mind service listen": "http://localhost:3486",
        "where does your knowledge service listen": "http://localhost:3484",
    }
    p = ScriptedPrompter(answers_dict)
    with redirect_stdout(io.StringIO()):
        result = run_flow(p, _DEFAULTS)
    services = result.org["org"]["services"]
    assert "mind" in services
    assert services["mind"]["host"] == "http://localhost:3486"
    assert services["mind"]["port"] == 3486
    assert "knowledge" in services
    assert "memory" not in services
    assert "senses" not in services


def test_optional_opt_ins_have_humble_why() -> None:
    """Per RAJ-63, each opt-in has a one-sentence WHY explanation."""
    for opt in OPTIONAL_OPT_INS:
        assert opt["why"]
        # Per PM directive: humble, no exaggeration.
        bad = ["revolutionary", "next-gen", "thrilled", "rethinking"]
        assert not any(b in opt["why"].lower() for b in bad)


def test_log_records_each_question() -> None:
    """The log captures every Q&A for replay debugging."""
    answers_dict = {
        "organization name": "Acme",
        "your name": "Vishal",
        "agent name": "Friday",
        "which ministers to enable": ["planning"],
        "optional sherpas to enable": [],
        "which protocols to enable": ["00", "12"],
        "ollama host": "http://localhost:11434",
        "cloud llm provider": "",
        "pick the organs your org runs": [],
    }
    p = ScriptedPrompter(answers_dict)
    with redirect_stdout(io.StringIO()):
        result = run_flow(p, _DEFAULTS)
    log = result.to_log()
    assert any(e["key"] == "org.name" for e in log)
    assert any(e["key"] == "org.agent.name" for e in log)
    assert all("ts" in e for e in log)
