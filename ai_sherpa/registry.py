"""Sherpa registry — discovers sherpas by walking the package directory.

A "sherpa" is any subdirectory of `ai_sherpa/` that contains a `manifest.toml`.
Discovery is lazy and filesystem-driven; adding a new sherpa = drop a new
directory. The CLI dispatcher (in __main__.py) uses this registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib  # py >= 3.11
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with open(path, "rb") as f:
        return tomllib.load(f)


@dataclass(frozen=True)
class Sherpa:
    """One sherpa — its directory + lazy view of its manifest."""

    name: str  # directory name, e.g. "boot"
    path: Path

    @cached_property
    def manifest(self) -> dict[str, Any]:
        """Parsed `manifest.toml`."""
        return _load_toml(self.path / "manifest.toml")

    @property
    def purpose(self) -> str:
        return self.manifest.get("sherpa", {}).get("purpose", "")

    @property
    def description(self) -> str:
        return self.purpose or self.name

    @property
    def language(self) -> str:
        return self.manifest.get("sherpa", {}).get("language", "python")

    @property
    def entry_point(self) -> str:
        return self.manifest.get("sherpa", {}).get("entry_point", "main.py")

    @property
    def model(self) -> str:
        return self.manifest.get("sherpa", {}).get("model", "")

    @property
    def status(self) -> str:
        return self.manifest.get("meta", {}).get("status", "active")


def _discover() -> dict[str, Sherpa]:
    """Walk `ai_sherpa/`, return every directory with a manifest.toml."""
    out: dict[str, Sherpa] = {}
    for child in sorted(_PACKAGE_DIR.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(("_", ".")) and child.name != "_scaffold":
            continue
        manifest = child / "manifest.toml"
        if manifest.is_file():
            out[child.name] = Sherpa(name=child.name, path=child)
    return out


def all_sherpas() -> dict[str, Sherpa]:
    """Return a fresh `{name: Sherpa}` dict (re-walks the directory)."""
    return _discover()


def get(name: str) -> Sherpa:
    """Look up a sherpa by directory name. Raises KeyError if unknown."""
    registry = _discover()
    if name not in registry:
        raise KeyError(f"unknown sherpa: {name!r}. Known: {sorted(registry)}")
    return registry[name]
