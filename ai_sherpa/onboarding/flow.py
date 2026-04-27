"""Onboarding Q&A flow.

Eight sections walking the user through the RAJ-50 variable registry.
Per PM directive (RAJ-43): humble voice, no exaggeration, explain WHY
each section matters in one sentence.

Two execution modes:
* Interactive — uses `questionary` to prompt the user. Defaults from
  ai-constitution's defaults.toml are pre-filled.
* Scripted — reads a TOML answers fixture (for tests, automation, or
  re-running with the same inputs).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────
# Section copy — humble framing, why-first.
# ──────────────────────────────────────────────────────────


SECTION_INTROS: dict[str, str] = {
    "org": (
        "Your organization's identity. Drives every reference to the org throughout "
        "the constitution, agent files, and protocols."
    ),
    "pm": (
        "The human at the top — the person whose authority the agent operates under. "
        "Article I says the principal is in charge."
    ),
    "agent": (
        "The AI Principal Secretary — name, persona, identity. This is the highest-leverage "
        "variable in the framework: 222 references to the agent's name across the source files."
    ),
    "ministries": (
        "Ministers are domain-expert prompt skills the agent delegates to. Pick which ones "
        "your org needs. You can add or remove later — re-run onboarding to update."
    ),
    "sherpas": (
        "Sherpas are execution scripts. Boot, exit, and onboarding are mandatory (always on). "
        "Pick which optional ones to wire up."
    ),
    "protocols": (
        "Protocols are operating procedures the constitution refers to. The default minimum "
        "is delegation (00) and cognitive continuity (12). Add more if your org runs them."
    ),
    "llm": (
        "Where your agent's models live. Local via Ollama by default; optional cloud provider."
    ),
    "services": (
        "Optional service endpoints — only set the ones your org actually runs. "
        "These are the nervous-system organs (mind, memory, senses, knowledge, dashboard)."
    ),
}


# ──────────────────────────────────────────────────────────
# Optional opt-ins — RAJ-63: humble WHY for each.
# ──────────────────────────────────────────────────────────


OPTIONAL_OPT_INS: list[dict[str, str]] = [
    {
        "key": "mind",
        "label": "mind organ",
        "why": (
            "Adds a routing layer between the agent and the LLMs — useful if you want "
            "T1 instant / T2 local / T3 escalate behavior."
        ),
        "needs_url": True,
        "url_prompt": "Where does your mind service listen?",
        "url_default": "http://localhost:3486",
    },
    {
        "key": "memory",
        "label": "memory organ",
        "why": (
            "Persistent state across sessions — useful if your agent should remember "
            "decisions made days ago."
        ),
        "needs_url": True,
        "url_prompt": "Where does your memory service listen?",
        "url_default": "http://localhost:3488",
    },
    {
        "key": "senses",
        "label": "senses organ",
        "why": (
            "Hardware integrations (camera, mic, mobile) — useful if your agent reads from "
            "physical devices."
        ),
        "needs_url": True,
        "url_prompt": "Where does your senses service listen?",
        "url_default": "http://localhost:3487",
    },
    {
        "key": "knowledge",
        "label": "knowledge organ",
        "why": (
            "Tool and capability registry — useful if your agent juggles many external tools."
        ),
        "needs_url": True,
        "url_prompt": "Where does your knowledge service listen?",
        "url_default": "http://localhost:3484",
    },
    {
        "key": "dashboard",
        "label": "dashboard organ",
        "why": (
            "Web UI for monitoring agent activity — useful if you want a visible status panel."
        ),
        "needs_url": True,
        "url_prompt": "Where does your dashboard listen?",
        "url_default": "http://localhost:3482",
    },
]


# ──────────────────────────────────────────────────────────
# Answer container.
# ──────────────────────────────────────────────────────────


@dataclass
class Answers:
    """In-memory record of everything the user said. Serializes to org-config.toml."""

    org: dict[str, Any] = field(default_factory=dict)
    log: list[dict[str, Any]] = field(default_factory=list)

    def record(self, key: str, value: Any) -> None:
        from datetime import datetime, timezone

        self.log.append(
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "key": key,
                "value": value,
            },
        )

    def to_org_dict(self) -> dict[str, Any]:
        return {"org": self.org}

    def to_log(self) -> list[dict[str, Any]]:
        return list(self.log)


# ──────────────────────────────────────────────────────────
# Prompter — abstracts questionary so we can swap to scripted mode.
# ──────────────────────────────────────────────────────────


class Prompter:
    """Abstract prompter — interactive or scripted. Subclasses override ask*."""

    def text(self, message: str, default: str = "") -> str:
        raise NotImplementedError

    def confirm(self, message: str, default: bool = False) -> bool:
        raise NotImplementedError

    def select(self, message: str, choices: list[str], default: str | None = None) -> str:
        raise NotImplementedError

    def checkbox(
        self,
        message: str,
        choices: list[str],
        defaults: list[str] | None = None,
        choice_descriptions: dict[str, str] | None = None,
    ) -> list[str]:
        raise NotImplementedError


class InteractivePrompter(Prompter):
    def __init__(self) -> None:
        try:
            import questionary  # noqa: F401
        except ImportError as exc:
            raise SystemExit(
                "error: questionary is required for interactive onboarding.\n"
                "fix: pip install 'ai-sherpa[onboarding]'  OR  pip install questionary",
            ) from exc

    def text(self, message: str, default: str = "") -> str:
        import questionary

        return questionary.text(message, default=default).unsafe_ask()

    def confirm(self, message: str, default: bool = False) -> bool:
        import questionary

        return bool(questionary.confirm(message, default=default).unsafe_ask())

    def select(self, message: str, choices: list[str], default: str | None = None) -> str:
        import questionary

        kwargs: dict[str, Any] = {"choices": choices}
        if default is not None and default in choices:
            kwargs["default"] = default
        return questionary.select(message, **kwargs).unsafe_ask()

    def checkbox(
        self,
        message: str,
        choices: list[str],
        defaults: list[str] | None = None,
        choice_descriptions: dict[str, str] | None = None,
    ) -> list[str]:
        import questionary

        defaults_set = set(defaults or [])
        items = [
            questionary.Choice(
                title=(
                    f"{c}  —  {choice_descriptions[c]}"
                    if choice_descriptions and c in choice_descriptions
                    else c
                ),
                value=c,
                checked=(c in defaults_set),
            )
            for c in choices
        ]
        return list(questionary.checkbox(message, choices=items).unsafe_ask())


class ScriptedPrompter(Prompter):
    """Reads pre-baked answers from a dict. Used for tests + non-interactive runs.

    The dict shape mirrors the question keys used by `run_flow` — see
    `tests/fixtures/raj-sadan-answers.toml` for the canonical example.
    """

    def __init__(self, answers: dict[str, Any]) -> None:
        self._answers = answers

    def _get(self, key: str, default: Any) -> Any:
        if key not in self._answers:
            return default
        return self._answers[key]

    def text(self, message: str, default: str = "") -> str:
        return str(self._get(self._key(message), default))

    def confirm(self, message: str, default: bool = False) -> bool:
        return bool(self._get(self._key(message), default))

    def select(self, message: str, choices: list[str], default: str | None = None) -> str:
        return str(self._get(self._key(message), default or choices[0]))

    def checkbox(
        self,
        message: str,
        choices: list[str],
        defaults: list[str] | None = None,
        choice_descriptions: dict[str, str] | None = None,
    ) -> list[str]:
        v = self._get(self._key(message), defaults or [])
        return list(v)

    def _key(self, message: str) -> str:
        """Normalize a question prompt to a stable lookup key."""
        # Strip everything after the colon and surrounding punctuation.
        import re

        s = message.strip()
        s = re.sub(r"\s*\(.*?\)\s*", " ", s)
        s = s.split(":")[0]
        s = re.sub(r"[^a-z0-9 ]+", "", s.lower())
        return s.strip()


# ──────────────────────────────────────────────────────────
# Section runners — each one collects one section's answers.
# ──────────────────────────────────────────────────────────


def _intro(section: str) -> None:
    """Print a section header + the WHY in one line."""
    print(f"\n— {section.upper()} —")
    print(f"  {SECTION_INTROS.get(section, '')}\n")


def section_org(p: Prompter, defaults: dict[str, Any], a: Answers) -> None:
    _intro("org")
    o = a.org.setdefault("org", {})
    o["name"] = p.text("Organization name", default=defaults.get("name", ""))
    a.record("org.name", o["name"])
    o["slug"] = p.text(
        "Slug (lowercase, kebab-case; leave empty to auto-derive)",
        default=defaults.get("slug", ""),
    )
    a.record("org.slug", o["slug"])
    o["tagline"] = p.text("Tagline (optional)", default=defaults.get("tagline", ""))
    a.record("org.tagline", o["tagline"])
    o["timezone"] = p.text(
        "Timezone (IANA, e.g. Europe/Madrid, Asia/Kolkata)",
        default=defaults.get("timezone", "UTC"),
    )
    a.record("org.timezone", o["timezone"])


def section_pm(p: Prompter, defaults: dict[str, Any], a: Answers) -> None:
    _intro("pm")
    pm = a.org.setdefault("org", {}).setdefault("pm", {})
    pm["name"] = p.text("Your name (the human at the top)", default=defaults.get("name", ""))
    a.record("org.pm.name", pm["name"])
    pm["title"] = p.text("Your title", default=defaults.get("title", "Prime Minister"))
    a.record("org.pm.title", pm["title"])
    pm["pronouns"] = p.text("Your pronouns", default=defaults.get("pronouns", "they/them"))
    a.record("org.pm.pronouns", pm["pronouns"])
    pm["email"] = p.text("Email (optional)", default=defaults.get("email", ""))
    a.record("org.pm.email", pm["email"])


def section_agent(p: Prompter, defaults: dict[str, Any], a: Answers) -> None:
    _intro("agent")
    ag = a.org.setdefault("org", {}).setdefault("agent", {})
    ag["name"] = p.text(
        "Agent name (e.g. Friday, Mr. V, Aria)", default=defaults.get("name", "Secretary"),
    )
    a.record("org.agent.name", ag["name"])
    ag["title"] = p.text(
        "Agent title", default=defaults.get("title", "Principal Secretary"),
    )
    a.record("org.agent.title", ag["title"])
    ag["emoji"] = p.text("Agent emoji", default=defaults.get("emoji", "🏛️"))
    a.record("org.agent.emoji", ag["emoji"])

    persona_defaults = defaults.get("persona", {})
    persona = ag.setdefault("persona", {})
    persona["tone"] = p.text(
        "Persona tone keywords",
        default=persona_defaults.get("tone", "Sharp, composed, efficient"),
    )
    a.record("org.agent.persona.tone", persona["tone"])
    keywords_default = ", ".join(
        persona_defaults.get("keywords", ["architect", "orchestrator"]),
    )
    keywords_str = p.text(
        "Persona keywords (comma-separated)", default=keywords_default,
    )
    persona["keywords"] = [k.strip() for k in keywords_str.split(",") if k.strip()]
    a.record("org.agent.persona.keywords", persona["keywords"])


def section_ministries(p: Prompter, defaults: dict[str, Any], a: Answers) -> None:
    _intro("ministries")
    KNOWN = ["planning", "design", "external_affairs", "resources", "review"]
    enabled = p.checkbox(
        "Which ministers to enable",
        choices=KNOWN,
        defaults=defaults.get("enabled", KNOWN),
    )
    a.org.setdefault("org", {}).setdefault("ministries", {})["enabled"] = enabled
    a.record("org.ministries.enabled", enabled)


def section_sherpas(p: Prompter, defaults: dict[str, Any], a: Answers) -> None:
    _intro("sherpas")
    OPTIONAL = ["scrum", "design", "nextcloud", "crawler"]
    print("Mandatory sherpas: boot, exit, onboarding (always enabled).")
    enabled_optional = p.checkbox(
        "Optional sherpas to enable",
        choices=OPTIONAL,
        defaults=[s for s in defaults.get("enabled", []) if s in OPTIONAL],
    )
    enabled = ["boot", "exit", "onboarding"] + enabled_optional
    a.org.setdefault("org", {}).setdefault("sherpas", {})["enabled"] = enabled
    a.record("org.sherpas.enabled", enabled)


def section_protocols(p: Prompter, defaults: dict[str, Any], a: Answers) -> None:
    _intro("protocols")
    KNOWN = [f"{i:02d}" for i in range(13)]
    enabled = p.checkbox(
        "Which protocols to enable",
        choices=KNOWN,
        defaults=defaults.get("enabled", ["00", "12"]),
    )
    a.org.setdefault("org", {}).setdefault("protocols", {})["enabled"] = enabled
    a.record("org.protocols.enabled", enabled)


def section_llm(p: Prompter, defaults: dict[str, Any], a: Answers) -> None:
    _intro("llm")
    llm = a.org.setdefault("org", {}).setdefault("llm", {})
    local = llm.setdefault("local", {})
    local["host"] = p.text(
        "Ollama host", default=defaults.get("local", {}).get("host", "http://localhost:11434"),
    )
    a.record("org.llm.local.host", local["host"])

    cloud = llm.setdefault("cloud", {})
    cloud["provider"] = p.select(
        "Cloud LLM provider (optional)",
        choices=["", "anthropic", "openai"],
        default=defaults.get("cloud", {}).get("provider", ""),
    )
    a.record("org.llm.cloud.provider", cloud["provider"])
    if cloud["provider"]:
        # Don't take the actual API key during onboarding — leave the slot empty
        # for the consumer to wire through their own secrets manager.
        cloud["api_key"] = ""
        print(
            "  Note: api_key is left empty — wire it through your own secrets manager "
            "(env var, encrypted credentials file, etc.). The schema slot is reserved.",
        )


def section_services(p: Prompter, defaults: dict[str, Any], a: Answers) -> None:
    """Surface the optional opt-ins (RAJ-63)."""
    _intro("services")
    services = a.org.setdefault("org", {}).setdefault("services", {})
    print("Optional opt-ins (each one is a separate organ you may run):\n")

    choices = [opt["key"] for opt in OPTIONAL_OPT_INS]
    descriptions = {opt["key"]: opt["why"] for opt in OPTIONAL_OPT_INS}
    enabled = p.checkbox(
        "Pick the organs your org runs",
        choices=choices,
        defaults=[],
        choice_descriptions=descriptions,
    )
    a.record("org.services.opt_ins", enabled)

    by_key = {opt["key"]: opt for opt in OPTIONAL_OPT_INS}
    for key in enabled:
        opt = by_key[key]
        if opt.get("needs_url"):
            host = p.text(opt["url_prompt"], default=opt["url_default"])
            # Parse port from URL if it has one
            from urllib.parse import urlparse

            parsed = urlparse(host)
            services[key] = {"host": host, "port": parsed.port or 0}
            a.record(f"org.services.{key}.host", host)


# ──────────────────────────────────────────────────────────
# Public entry — runs the whole flow.
# ──────────────────────────────────────────────────────────


def run_flow(prompter: Prompter, defaults: dict[str, Any]) -> Answers:
    """Walk the eight sections, return the populated Answers."""
    a = Answers()
    org_defaults = defaults.get("org", {})

    section_org(prompter, org_defaults, a)
    section_pm(prompter, org_defaults.get("pm", {}), a)
    section_agent(prompter, org_defaults.get("agent", {}), a)
    section_ministries(prompter, org_defaults.get("ministries", {}), a)
    section_sherpas(prompter, org_defaults.get("sherpas", {}), a)
    section_protocols(prompter, org_defaults.get("protocols", {}), a)
    section_llm(prompter, org_defaults.get("llm", {}), a)
    section_services(prompter, org_defaults.get("services", {}), a)

    return a
