"""Tests for ai_sherpa.onboarding.validation (RAJ-62)."""

from __future__ import annotations

from ai_sherpa.onboarding.validation import (
    auto_derive,
    derive_slug,
    required_failures,
    validate,
)


# ──────────────────────────────────────────────────────────
# Slug derivation.
# ──────────────────────────────────────────────────────────


def test_derive_slug_basic() -> None:
    assert derive_slug("Acme") == "acme"
    assert derive_slug("Raj Sadan") == "raj-sadan"
    assert derive_slug("FOO BAR INC.") == "foo-bar-inc"


def test_derive_slug_strips_special_chars() -> None:
    assert derive_slug("My_Cool/Org") == "my-cool-org"
    assert derive_slug("a&b&c") == "a-b-c"
    assert derive_slug("--leading--trailing--") == "leading-trailing"


# ──────────────────────────────────────────────────────────
# auto_derive.
# ──────────────────────────────────────────────────────────


def test_auto_derive_fills_empty_slug() -> None:
    answers = {"org": {"name": "Acme", "slug": "", "agent": {}, "pm": {}}}
    auto_derive(answers)
    assert answers["org"]["slug"] == "acme"


def test_auto_derive_does_not_override_explicit_slug() -> None:
    answers = {"org": {"name": "Acme", "slug": "acme-corp", "agent": {}, "pm": {}}}
    auto_derive(answers)
    assert answers["org"]["slug"] == "acme-corp"


def test_auto_derive_fills_reports_to_from_pm_title() -> None:
    answers = {"org": {"name": "Acme", "pm": {"title": "Founder"}, "agent": {}}}
    auto_derive(answers)
    assert answers["org"]["agent"]["reports_to"] == "Founder"


# ──────────────────────────────────────────────────────────
# Validation: required fields.
# ──────────────────────────────────────────────────────────


def test_validate_required_failures_on_placeholder_org_name() -> None:
    answers = {
        "org": {
            "name": "Your Org",
            "pm": {"name": "Vishal"},
            "agent": {"name": "Mr. V"},
        },
    }
    issues = validate(answers)
    failures = required_failures(issues)
    assert any(f.field == "org.name" for f in failures)


def test_validate_required_failures_on_empty_pm_name() -> None:
    answers = {
        "org": {
            "name": "Acme",
            "pm": {"name": ""},
            "agent": {"name": "Friday"},
        },
    }
    failures = required_failures(validate(answers))
    assert any(f.field == "org.pm.name" for f in failures)


def test_validate_required_failures_on_default_agent_name() -> None:
    answers = {
        "org": {
            "name": "Acme",
            "pm": {"name": "Vishal"},
            "agent": {"name": "Secretary"},  # placeholder
        },
    }
    failures = required_failures(validate(answers))
    assert any(f.field == "org.agent.name" for f in failures)


def test_validate_passes_with_real_values() -> None:
    answers = {
        "org": {
            "name": "Acme",
            "pm": {"name": "Vishal"},
            "agent": {"name": "Friday"},
        },
    }
    failures = required_failures(validate(answers))
    assert failures == []


def test_validate_format_error_on_bad_slug() -> None:
    answers = {
        "org": {
            "name": "Acme",
            "slug": "Acme Corp",  # uppercase + space — not kebab-case
            "pm": {"name": "Vishal"},
            "agent": {"name": "Friday"},
        },
    }
    issues = validate(answers)
    assert any(i.field == "org.slug" and i.kind == "format" for i in issues)


def test_validate_warns_on_default_timezone() -> None:
    answers = {
        "org": {
            "name": "Acme",
            "timezone": "UTC",
            "pm": {"name": "Vishal"},
            "agent": {"name": "Friday"},
        },
    }
    issues = validate(answers)
    warns = [i for i in issues if i.kind == "warn"]
    assert any(w.field == "org.timezone" for w in warns)
