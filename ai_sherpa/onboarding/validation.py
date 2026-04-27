"""Validation rules for the onboarding sherpa (RAJ-62).

Per RAJ-50 the schema accepts placeholder values like `org.name = "Your Org"`.
The onboarding sherpa is the layer that refuses to proceed with those
placeholders interactively. This module:

* Defines which fields are required (hard refuses placeholder).
* Defines which fields are recommended (soft warns, lets user proceed).
* Auto-derives slug from name when slug is empty.
* Auto-fills agent.reports_to from pm.title when empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ──────────────────────────────────────────────────────────
# Fields that hard-refuse placeholders.
# ──────────────────────────────────────────────────────────

# Hard-required fields with their placeholder values.
# If the user leaves these as defaults, onboarding refuses to proceed.
HARD_REQUIRED: dict[str, set[str]] = {
    "org.name": {"", "Your Org"},
    "org.pm.name": {""},
    "org.agent.name": {"", "Secretary"},  # the highest-leverage variable
}

# Soft-warn fields — onboarding proceeds but prints a warning.
SOFT_WARN: dict[str, set[str]] = {
    "org.timezone": {"UTC"},  # most users aren't in UTC
}


_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


@dataclass
class ValidationIssue:
    """One validation problem."""

    kind: str       # "required" | "warn" | "format"
    field: str
    message: str


def derive_slug(name: str) -> str:
    """Derive a kebab-case slug from a human name. Mirrors render._derive_slug."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:64]


def _resolve(d: dict[str, Any], path: str) -> Any:
    """Read a dotted path from a nested dict. Returns None if missing."""
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set(d: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: dict[str, Any] = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def auto_derive(answers: dict[str, Any]) -> None:
    """Apply auto-derivations in place: slug from name, reports_to from pm.title.

    Only fills empties; never overrides a value the user provided.
    """
    name = _resolve(answers, "org.name") or ""
    slug = _resolve(answers, "org.slug") or ""
    if not slug and name:
        _set(answers, "org.slug", derive_slug(name))

    reports = _resolve(answers, "org.agent.reports_to") or ""
    pm_title = _resolve(answers, "org.pm.title") or ""
    if not reports and pm_title:
        _set(answers, "org.agent.reports_to", pm_title)


def validate(answers: dict[str, Any]) -> list[ValidationIssue]:
    """Return a list of problems with the answers. Empty list = OK."""
    issues: list[ValidationIssue] = []

    for field, placeholders in HARD_REQUIRED.items():
        v = _resolve(answers, field)
        if v is None or (isinstance(v, str) and v in placeholders):
            issues.append(
                ValidationIssue(
                    kind="required",
                    field=field,
                    message=(
                        f"{field!r} must be set to a real value (you have {v!r})."
                    ),
                ),
            )

    for field, placeholders in SOFT_WARN.items():
        v = _resolve(answers, field)
        if isinstance(v, str) and v in placeholders:
            issues.append(
                ValidationIssue(
                    kind="warn",
                    field=field,
                    message=f"{field!r} is still {v!r}. Consider setting your real timezone.",
                ),
            )

    slug = _resolve(answers, "org.slug")
    if slug and not _SLUG_RE.match(str(slug)):
        issues.append(
            ValidationIssue(
                kind="format",
                field="org.slug",
                message=(
                    f"org.slug {slug!r} must be lowercase kebab-case "
                    "(alphanumeric + hyphen, 1-64 chars)."
                ),
            ),
        )

    return issues


def required_failures(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [i for i in issues if i.kind in ("required", "format")]


def soft_warnings(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [i for i in issues if i.kind == "warn"]
