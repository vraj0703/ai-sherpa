"""ai-sherpa CLI — dispatch to a named sherpa or generate a new one.

Usage:
    python -m ai_sherpa                          (lists available sherpas)
    python -m ai_sherpa <name> [args...]         (runs that sherpa's main.py)
    python -m ai_sherpa scaffold <new-name>      (creates a new sherpa from the _scaffold template)
    python -m ai_sherpa --version
    python -m ai_sherpa --help
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .registry import all_sherpas, get


def _print_help() -> None:
    print(_HELP)
    print("\nAvailable sherpas:")
    for s in all_sherpas().values():
        if s.name == "_scaffold":
            continue
        suffix = "" if s.status == "active" else f"  [{s.status}]"
        print(f"  {s.name:<14} {s.purpose}{suffix}")


_HELP = """\
ai-sherpa — execution sherpas for AI agents built on ai-constitution.

Usage:
  python -m ai_sherpa                       list available sherpas
  python -m ai_sherpa <name> [args...]      run a sherpa
  python -m ai_sherpa scaffold <new-name>   generate a new sherpa from the template
  python -m ai_sherpa --version
  python -m ai_sherpa --help\
"""


def _scaffold(new_name: str) -> int:
    """Copy the _scaffold/ template into a new sherpa directory."""
    target_dir = Path(__file__).resolve().parent / new_name
    if target_dir.exists():
        print(f"error: target directory already exists: {target_dir}", file=sys.stderr)
        return 1
    template_dir = Path(__file__).resolve().parent / "_scaffold"
    if not template_dir.is_dir():
        print(f"error: scaffold template not found at {template_dir}", file=sys.stderr)
        return 2
    shutil.copytree(template_dir, target_dir)
    print(f"created new sherpa at {target_dir}")
    print(
        f"next:\n"
        f"  1. fill in placeholders in {target_dir.name}/manifest.toml\n"
        f"  2. implement {target_dir.name}/main.py\n"
        f"  3. set [meta] status to 'active' in manifest.toml when ready",
    )
    return 0


def _run_sherpa(name: str, argv: list[str]) -> int:
    """Run sherpa <name>'s main.py as a subprocess so its argv parsing is honored."""
    try:
        sherpa = get(name)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    entry = sherpa.path / sherpa.entry_point
    if not entry.is_file():
        print(f"error: entry point not found: {entry}", file=sys.stderr)
        return 2

    if sherpa.language != "python":
        print(f"error: only python sherpas supported in v0.0.1 (got {sherpa.language!r})", file=sys.stderr)
        return 2

    # Try import-and-call first — the recommended pattern for new sherpas.
    module_name = f"ai_sherpa.{name}.main"
    try:
        module = importlib.import_module(module_name)
    except (ImportError, ModuleNotFoundError):
        module = None

    if module is not None and hasattr(module, "main") and callable(module.main):
        try:
            return module.main(argv)
        except SystemExit as exc:
            return int(exc.code or 0)

    # Fallback — run as a subprocess (for sherpas that haven't been refactored yet).
    return subprocess.call([sys.executable, str(entry), *argv])


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _print_help()
        return 0
    if args[0] in ("-h", "--help"):
        _print_help()
        return 0
    if args[0] in ("-V", "--version"):
        print(f"ai-sherpa {__version__}")
        return 0
    if args[0] == "scaffold":
        if len(args) < 2:
            print("error: scaffold requires a new sherpa name", file=sys.stderr)
            return 2
        return _scaffold(args[1])

    return _run_sherpa(args[0], args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
