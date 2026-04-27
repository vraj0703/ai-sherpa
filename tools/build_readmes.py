#!/usr/bin/env python3
"""Generate per-sherpa README.md from each sherpa's manifest.toml.

Run after `convert_from_raj_sadan.py` so the sherpa directories exist.

Usage (from repo root):
    python tools/build_readmes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "ai_sherpa"


def _load_toml(path: Path) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with open(path, "rb") as f:
        return tomllib.load(f)


TEMPLATE = """\
# {title} Sherpa

> {purpose}

A sherpa from [ai-sherpa](https://github.com/vraj0703/ai-sherpa) — the standard library of execution sherpas for any organization built on [ai-constitution](https://github.com/vraj0703/ai-constitution).

## Authority

- Derives from: Constitution Article IV (Sherpas)
- Invoked by: {invoked_by}
- Reports to: invoker

## Quick start

```bash
{quick_start}
```

## Files

```
{name}/
├── manifest.toml   # metadata, deps, invocation contract
├── main.py         # entry point
{extra_files}
```

## Dependencies

| Group | Items |
|---|---|
| Python | {python_deps} |
| Ollama models | {ollama_models} |
| External | {external} |

## Status

`{status}` — see manifest.toml for full details.

## License

MIT — see the [LICENSE](../../LICENSE) at the repo root.

## See also

- [ai-constitution](https://github.com/vraj0703/ai-constitution) — the governance framework
- [ai-ministers](https://github.com/vraj0703/ai-ministers) — the policy layer that invokes sherpas
- [Sherpa index](../../README.md)
"""


def render_one(name: str, manifest: dict) -> str:
    sherpa = manifest.get("sherpa", {})
    invocation = manifest.get("invocation", {})
    deps = manifest.get("dependencies", {})
    meta = manifest.get("meta", {})

    title = name.replace("_", "-").title()
    purpose = sherpa.get("purpose", "(no purpose recorded)")
    invoked_by = ", ".join(sherpa.get("invoked_by", ["PM"]))
    command = invocation.get("command", f"python -m ai_sherpa {name}")
    args = invocation.get("args", [])
    quick_start_lines = [command]
    if args:
        quick_start_lines.append("# common flags: " + " ".join(args))
    quick_start = "\n".join(quick_start_lines)

    sherpa_dir = PACKAGE_DIR / name
    extra_files = []
    for p in sorted(sherpa_dir.glob("*")):
        if p.name in {"manifest.toml", "main.py", "README.md", "__pycache__"}:
            continue
        if p.is_file():
            extra_files.append(f"├── {p.name}")
    extra_block = "\n".join(extra_files) if extra_files else ""

    return TEMPLATE.format(
        title=title,
        purpose=purpose,
        invoked_by=invoked_by,
        quick_start=quick_start,
        name=name,
        extra_files=extra_block,
        python_deps=", ".join(deps.get("python_packages", [])) or "—",
        ollama_models=", ".join(deps.get("ollama_models", [])) or "—",
        external=", ".join(deps.get("external", [])) or "—",
        status=meta.get("status", "unknown"),
    )


def main() -> int:
    written = 0
    for child in sorted(PACKAGE_DIR.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.toml"
        if not manifest_path.is_file():
            continue
        manifest = _load_toml(manifest_path)
        target = child / "README.md"
        target.write_text(render_one(child.name, manifest), encoding="utf-8", newline="\n")
        print(f"  wrote {target.relative_to(REPO_ROOT)}")
        written += 1
    print(f"\nGenerated {written} READMEs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
