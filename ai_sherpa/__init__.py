"""ai-sherpa — execution sherpas for AI agents built on ai-constitution."""

from __future__ import annotations

__version__ = "0.1.0"

from .registry import Sherpa, all_sherpas, get  # noqa: E402

__all__ = ["Sherpa", "all_sherpas", "get", "__version__"]
