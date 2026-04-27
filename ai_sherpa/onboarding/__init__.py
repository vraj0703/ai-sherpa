"""Onboarding sherpa — walks a new org through the variable registry,
writes org-config.toml, renders the populated bundle.

Closes M3 (RAJ-61..RAJ-65). The integration test for the whole framework:
if onboarding works end-to-end against the constitutional templates and
produces a working bundle, the framework holds together.
"""

from . import main  # noqa: F401

__all__ = ["main"]
