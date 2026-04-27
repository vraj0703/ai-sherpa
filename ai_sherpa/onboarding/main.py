"""Onboarding sherpa CLI entry — interactive Q&A, scripted, or sync mode.

Closes M3:
* RAJ-61 — interactive Q&A flow (in flow.py)
* RAJ-62 — required/optional validation (in validation.py)
* RAJ-63 — optional opt-ins surfaced (in flow.py::OPTIONAL_OPT_INS)
* RAJ-64 — `sync` subcommand for re-render after framework upgrade
* RAJ-65 — non-interactive `--answers` mode for the e2e test

Usage:
    python -m ai_sherpa onboarding [--output DIR] [--config-out PATH]
                                   [--answers PATH] [--dry-run] [--verbose]
    python -m ai_sherpa onboarding sync [--output DIR] [--config PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _serialize_toml(d: dict[str, Any]) -> str:
    """Write a dict as TOML. We dogfood Python's lack of stdlib TOML writer.

    For the small subset onboarding produces (strings, ints, lists, nested dicts),
    a tiny serializer is sufficient and avoids a hard dep on `tomli-w`.
    """

    def _esc(s: str) -> str:
        # TOML basic strings — escape backslash + double-quote.
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def _scalar(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            return str(v)
        if isinstance(v, list):
            inner = ", ".join(_scalar(x) for x in v)
            return f"[{inner}]"
        return f'"{_esc(str(v))}"'

    lines: list[str] = []

    def _emit_table(prefix: str, table: dict[str, Any]) -> None:
        scalars: list[tuple[str, Any]] = []
        sub_tables: list[tuple[str, dict[str, Any]]] = []
        for k, v in table.items():
            if isinstance(v, dict):
                sub_tables.append((k, v))
            else:
                scalars.append((k, v))
        if scalars:
            if prefix:
                lines.append(f"\n[{prefix}]")
            for k, v in scalars:
                lines.append(f"{k} = {_scalar(v)}")
        for k, v in sub_tables:
            new_prefix = f"{prefix}.{k}" if prefix else k
            _emit_table(new_prefix, v)

    # Root scalars first (none expected for our shape, but tolerate).
    root_scalars = {k: v for k, v in d.items() if not isinstance(v, dict)}
    if root_scalars:
        for k, v in root_scalars.items():
            lines.append(f"{k} = {_scalar(v)}")
    for k, v in d.items():
        if isinstance(v, dict):
            _emit_table(k, v)

    return "\n".join(lines).lstrip("\n") + "\n"


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib  # py >= 3.11
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with open(path, "rb") as f:
        return tomllib.load(f)


def _load_defaults(constitution_dir: Path | None) -> dict[str, Any]:
    """Find ai-constitution's defaults.toml. Tries the package install first,
    then a sibling/submodule layout (common in raj-sadan-style monorepos)."""
    # Path 1: installed package
    try:
        from ai_constitution.render import BUILTIN_DEFAULTS_PATH

        if Path(BUILTIN_DEFAULTS_PATH).is_file():
            return _load_toml(Path(BUILTIN_DEFAULTS_PATH))
    except ImportError:
        pass

    # Path 2: explicit override
    if constitution_dir:
        p = constitution_dir / "defaults.toml"
        if p.is_file():
            return _load_toml(p)

    # Path 3: walk up looking for vendor/ai-constitution
    here = Path.cwd()
    for parent in [here, *here.parents]:
        candidate = parent / "vendor" / "ai-constitution" / "defaults.toml"
        if candidate.is_file():
            return _load_toml(candidate)

    print(
        "error: cannot find ai-constitution's defaults.toml.\n"
        "fix: pip install ai-constitution  OR  pass --constitution-dir <path>",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _render_bundle(
    org_config_path: Path,
    output_dir: Path,
    constitution_dir: Path | None,
) -> int:
    """Call ai_constitution.Constitution to render the populated bundle."""
    try:
        from ai_constitution.render import Constitution
    except ImportError:
        print(
            "error: ai_constitution package not importable.\n"
            "fix: pip install ai-constitution  OR  add vendor/ai-constitution to sys.path",
            file=sys.stderr,
        )
        return 2

    kwargs: dict[str, Any] = {"org_config_path": org_config_path}
    if constitution_dir:
        kwargs["templates_dir"] = constitution_dir / "templates"
        kwargs["defaults_path"] = constitution_dir / "defaults.toml"
        if (constitution_dir / "skills").is_dir():
            kwargs["skills_dir"] = constitution_dir / "skills"

    c = Constitution.load(**kwargs)
    rendered = c.render(output_dir)
    print(f"rendered {len(rendered)} files to {output_dir}")
    return 0


# ──────────────────────────────────────────────────────────
# `onboarding` command
# ──────────────────────────────────────────────────────────


def cmd_onboarding(args: argparse.Namespace) -> int:
    from .flow import (
        Answers,
        InteractivePrompter,
        Prompter,
        ScriptedPrompter,
        run_flow,
    )
    from .validation import (
        auto_derive,
        required_failures,
        soft_warnings,
        validate,
    )

    constitution_dir = Path(args.constitution_dir).resolve() if args.constitution_dir else None
    defaults = _load_defaults(constitution_dir)

    # Pick prompter: scripted if --answers given, else interactive.
    prompter: Prompter
    if args.answers:
        scripted = _load_toml(Path(args.answers))
        prompter = ScriptedPrompter(scripted.get("answers", scripted))
    else:
        prompter = InteractivePrompter()

    # Run the flow.
    try:
        answers = run_flow(prompter, defaults)
    except KeyboardInterrupt:
        print("\nonboarding cancelled.", file=sys.stderr)
        return 130

    # Auto-derive slug + reports_to.
    auto_derive(answers.org)

    # Validate.
    issues = validate(answers.org)
    failures = required_failures(issues)
    warnings = soft_warnings(issues)
    for w in warnings:
        print(f"warning: {w.message}")
    if failures:
        print("\nonboarding cannot proceed — the following must be set:", file=sys.stderr)
        for f in failures:
            print(f"  • {f.field}: {f.message}", file=sys.stderr)
        return 1

    # Decide where to write.
    output_dir = Path(args.output).resolve()
    config_out = (
        Path(args.config_out).resolve()
        if args.config_out
        else output_dir / "org-config.toml"
    )

    if args.dry_run:
        print("\n--- dry-run: org-config.toml content ---")
        print(_serialize_toml(answers.org))
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    config_out.parent.mkdir(parents=True, exist_ok=True)

    # Write org-config.toml.
    config_out.write_text(_serialize_toml(answers.org), encoding="utf-8", newline="\n")
    print(f"wrote {config_out}")

    # Write the .onboarding-log.json for replay debugging.
    log_path = config_out.parent / ".onboarding-log.json"
    log_path.write_text(
        json.dumps(
            {
                "completed_at": datetime.now(tz=timezone.utc).isoformat(),
                "events": answers.to_log(),
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    if args.verbose:
        print(f"wrote {log_path}")

    # Render the bundle.
    return _render_bundle(config_out, output_dir, constitution_dir)


# ──────────────────────────────────────────────────────────
# `onboarding sync` command (RAJ-64)
# ──────────────────────────────────────────────────────────


def cmd_sync(args: argparse.Namespace) -> int:
    """Re-render after a framework upgrade.

    Reads the existing org-config.toml, loads new defaults from the (possibly
    upgraded) ai-constitution, prompts for any new variables not in the user's
    config, re-renders.
    """
    constitution_dir = Path(args.constitution_dir).resolve() if args.constitution_dir else None
    defaults = _load_defaults(constitution_dir)

    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        print(f"error: existing config not found: {config_path}", file=sys.stderr)
        return 2
    existing = _load_toml(config_path)

    # Compare keys: anything in defaults that's missing in existing is "new".
    def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in d.items():
            new_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(_flatten(v, new_key))
            else:
                out[new_key] = v
        return out

    flat_defaults = _flatten(defaults)
    flat_existing = _flatten(existing)

    new_keys = [k for k in flat_defaults if k not in flat_existing]
    removed_keys = [k for k in flat_existing if k not in flat_defaults]

    if not new_keys and not removed_keys:
        print("no schema drift — re-rendering with existing config")
        return _render_bundle(config_path, Path(args.output).resolve(), constitution_dir)

    if new_keys:
        print(f"\nNew variables added by the framework upgrade ({len(new_keys)}):")
        for k in new_keys:
            print(f"  + {k} = {flat_defaults[k]!r}  (default)")
    if removed_keys:
        print(f"\nVariables removed by the framework upgrade ({len(removed_keys)}):")
        for k in removed_keys:
            print(f"  - {k}  (was {flat_existing[k]!r})")

    print("\nThe non-interactive sync accepts framework defaults for new keys")
    print("and drops removed keys. Re-run interactively for full Q&A on the new variables.")

    # For each new key, write the default into the existing config.
    for k in new_keys:
        # Walk into existing using the dotted path
        parts = k.split(".")
        cur = existing
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = flat_defaults[k]

    # Write the merged config back.
    config_path.write_text(_serialize_toml(existing), encoding="utf-8", newline="\n")
    print(f"\nupdated {config_path}")

    return _render_bundle(config_path, Path(args.output).resolve(), constitution_dir)


# ──────────────────────────────────────────────────────────
# argparse wiring + module entry
# ──────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="onboarding",
        description="Walk an org through the variable registry; render the bundle.",
    )
    sub = p.add_subparsers(dest="cmd")

    pp = sub.add_parser("run", help="run the interactive Q&A flow (default)")
    pp.add_argument("--output", default="./out", help="where to render the bundle")
    pp.add_argument("--config-out", help="where to write org-config.toml (default: <output>/org-config.toml)")
    pp.add_argument("--answers", help="non-interactive: read answers from this TOML fixture")
    pp.add_argument("--constitution-dir", help="path to ai-constitution checkout (overrides pip install)")
    pp.add_argument("--dry-run", action="store_true", help="walk prompts; print the would-be config; do not write")
    pp.add_argument("--verbose", action="store_true")
    pp.set_defaults(func=cmd_onboarding)

    sp = sub.add_parser("sync", help="re-render after a framework upgrade")
    sp.add_argument("--config", required=True, help="existing org-config.toml")
    sp.add_argument("--output", default="./out", help="where to render the bundle")
    sp.add_argument("--constitution-dir", help="path to ai-constitution checkout (overrides pip install)")
    sp.set_defaults(func=cmd_sync)

    return p


def main(argv: list[str] | None = None) -> int:
    # Reconfigure stdout to UTF-8 on Windows so emoji + non-ASCII content
    # in printed TOML / status lines doesn't crash the cp1252 console.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass

    args = list(sys.argv[1:] if argv is None else argv)
    # No subcommand → default to `run`.
    parser = _build_parser()
    if not args or (args and args[0] not in {"run", "sync", "-h", "--help"}):
        # Default to `run` so plain `python -m ai_sherpa onboarding` is interactive.
        args = ["run", *args]
    parsed = parser.parse_args(args)
    if not getattr(parsed, "func", None):
        parser.print_help()
        return 0
    return parsed.func(parsed)


if __name__ == "__main__":
    raise SystemExit(main())
