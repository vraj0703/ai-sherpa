#!/usr/bin/env python3
"""
Design Sherpa — main.py
Authority: CONSTITUTION.toml, Article IV

Purpose: Accept design requests from other sherpas and system components.
         Serve the Raj Sadan UI/UX component index.
         Generate production-ready HTML/CSS code via local LLM.

Usage:
  python sherpa/design/main.py --list
  python sherpa/design/main.py --component button
  python sherpa/design/main.py --token color-primary-500
  python sherpa/design/main.py --request "I need a status dashboard card"
  python sherpa/design/main.py --dry-run
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path

import requests

# ─── Paths ───
SHERPA_DIR   = Path(__file__).parent
PROJECT_ROOT = SHERPA_DIR.parent.parent
INDEX_FILE   = SHERPA_DIR / "component-index.toml"
DESIGN_SYS   = PROJECT_ROOT / ".claude/skills/ministry-design/references/design-system.toml"
DESIGN_TOK   = PROJECT_ROOT / ".claude/skills/ministry-design/references/design-tokens.toml"
CSS_FILE     = PROJECT_ROOT / "design-system/dist/raj-sadan.css"
REF_COLORS   = PROJECT_ROOT / "design-system/tokens/colors.toml"
REF_TYPO     = PROJECT_ROOT / "design-system/tokens/typography.toml"
REF_STYLES   = PROJECT_ROOT / "design-system/tokens/styles.toml"

# ─── LLM Config ───
OLLAMA_HOST    = "http://localhost:11434"
PRIMARY_MODEL  = "qwen2.5-coder:7b"
FALLBACK_MODEL = "qwen2.5:14b"

SYSTEM_PROMPT = """You are the Design Sherpa of Raj Sadan. Your job is to help sherpas and developers build UI correctly using the Raj Sadan Design System.

DESIGN SYSTEM RULES:
- CSS file to include: design-system/dist/raj-sadan.css
- All CSS variables use --rs- prefix
- Colors: --rs-color-{palette}-{shade} (primary=Royal Indigo, secondary=Sovereign Gold, neutral=Slate)
- Surfaces: --rs-surface-background/primary/raised/border | --rs-text-primary/secondary/disabled
- Spacing (4px grid): --rs-space-2xs/xs/sm/md/lg/xl/2xl/3xl = 2/4/8/16/24/32/48/64px
- Typography: --rs-font-size-display/h1/h2/h3/h4/h5/h6/body-lg/body/body-sm/caption/overline
- Shadows: --rs-shadow-flat/low/medium/high/overlay
- Radii: --rs-radius-none/sm/md/lg/xl/full = 0/4/8/12/16/9999px
- Dark mode: add .rs-theme-dark to <html> or <body>

COMPONENT CLASSES:
- Button: .rs-btn + .rs-btn-primary/secondary/ghost/danger
- Card: .rs-card + .rs-card-ministry/.rs-card-task
- Badge: .rs-badge + .rs-badge-active/pending/completed/error/suspended
- Table: .rs-table, .rs-th, .rs-td, .rs-tr, .rs-table-container
- Navigation: .rs-nav-sidebar or .rs-nav-topbar + .rs-nav-item + .rs-nav-item-active
- Alert: .rs-alert + .rs-alert-info/success/warning/error
- Input: .rs-field, .rs-label, .rs-input, .rs-select, .rs-checkbox, .rs-checkbox-label
- Avatar: .rs-avatar + .rs-avatar-xs/sm/md/lg/xl + .rs-avatar-user/.rs-avatar-ministry

ACCESSIBILITY RULES (always apply):
- Buttons: aria-disabled='true' when disabled (not just disabled attr)
- Alerts: role='alert' + aria-live='assertive' for errors/warnings; role='status' + polite for info/success
- Tables: scope='col' on th; aria-sort for sortable columns
- Inputs: always visible <label>, aria-describedby for errors, aria-required='true'
- Navigation: aria-current='page' on active item; skip-link before topbar
- Avatars: aria-label with entity name

ADDITIONAL REFERENCES:
- design-system/tokens/colors.toml — 161 curated color palettes for theming and custom components
- design-system/tokens/typography.toml — 73 font pairings for headings, body, and display text
- design-system/tokens/styles.toml — 84 UI style presets (glassmorphism, neumorphism, brutalist, etc.)

OUTPUT FORMAT:
1. RECOMMENDATION — which component(s) to use and why (2-3 sentences)
2. HTML CODE — complete, copy-pasteable HTML using the design system classes
3. NOTES — any important implementation details (max 3 bullet points)"""


def load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def check_ollama() -> str:
    """Return the best available model, or PRIMARY_MODEL if check fails."""
    try:
        resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        resp.raise_for_status()
        available = [m["name"] for m in resp.json().get("models", [])]
        for model in [PRIMARY_MODEL, FALLBACK_MODEL]:
            if any(model in m for m in available):
                return model
    except Exception:
        pass
    return PRIMARY_MODEL


def ollama_generate(prompt: str, model: str) -> tuple[str, bool]:
    """Call Ollama. Returns (response_text, success)."""
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": model, "prompt": prompt, "system": SYSTEM_PROMPT, "stream": False},
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip(), True
    except requests.exceptions.Timeout:
        return "ERROR: LLM request timed out (90s)", False
    except Exception as e:
        return f"ERROR: {e}", False


def print_separator(title: str = "") -> None:
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print("=" * pad + f" {title} " + "=" * pad)
    else:
        print("=" * width)


# ─── Commands ───

def cmd_dry_run(index: dict) -> None:
    print_separator("Design Sherpa — Dry Run")
    checks = [
        ("Component index", INDEX_FILE.exists()),
        ("Design system spec", DESIGN_SYS.exists()),
        ("Design tokens", DESIGN_TOK.exists()),
        ("CSS framework", CSS_FILE.exists()),
        ("Color palettes (161)", REF_COLORS.exists()),
        ("Typography pairings (73)", REF_TYPO.exists()),
        ("UI style presets (84)", REF_STYLES.exists()),
    ]
    all_ok = True
    for name, ok in checks:
        status = "OK" if ok else "MISSING"
        print(f"  {status:8s}  {name}")
        if not ok:
            all_ok = False

    comp_count = len(index.get("components", {}))
    print(f"  {'OK':8s}  Component index ({comp_count} components loaded)")
    print(f"  {'INFO':8s}  Ollama host: {OLLAMA_HOST}")
    print(f"  {'INFO':8s}  Primary model: {PRIMARY_MODEL}")
    print(f"  {'INFO':8s}  Fallback model: {FALLBACK_MODEL}")
    print_separator()
    print(f"STATUS: {'SUCCESS' if all_ok else 'PARTIAL — missing files above'}")


def cmd_list(index: dict) -> None:
    print_separator("Design Sherpa — Component Index")
    components = index.get("components", {})
    for name, comp in components.items():
        print(f"\n  [{name}]")
        print(f"    Description : {comp.get('description', 'N/A')}")
        variants = comp.get("variants") or comp.get("sizes", [])
        print(f"    Variants    : {', '.join(variants)}")
        print(f"    CSS Class   : {comp.get('css_class', 'N/A')}")
        tokens = comp.get("key_tokens", [])
        print(f"    Key Tokens  : {', '.join(tokens[:3])}{'...' if len(tokens) > 3 else ''}")

    print(f"\n  [TOKEN CATEGORIES]")
    for cat, desc in index.get("token_categories", {}).items():
        print(f"    {cat:12s}  {desc[:70]}{'...' if len(desc) > 70 else ''}")

    print(f"\n  [FILES]")
    print(f"    design-system/dist/raj-sadan.css  —  CSS framework (include in any Raj Sadan UI)")
    print(f"    design-system/dist/tokens.json    —  Programmatic token access")
    print_separator()
    print(f"STATUS: SUCCESS | {len(components)} components indexed")


def cmd_component(name: str, index: dict) -> None:
    # Normalize aliases
    aliases = {
        "status-badge": "status_badge",
        "statusbadge": "status_badge",
        "datatable": "data_table",
        "data-table": "data_table",
        "nav": "navigation",
        "navbar": "navigation",
    }
    name = aliases.get(name.lower(), name.lower().replace("-", "_"))
    components = index.get("components", {})
    comp = components.get(name)

    if not comp:
        print(f"ERROR: Component '{name}' not found.")
        print(f"Available: {', '.join(components.keys())}")
        sys.exit(1)

    print_separator(f"Design Sherpa — {name}")
    print(f"  Description : {comp.get('description', 'N/A')}")
    variants = comp.get("variants") or comp.get("sizes", [])
    print(f"  Variants    : {', '.join(variants)}")
    print(f"  CSS Class   : {comp.get('css_class', 'N/A')}")
    print(f"  Key Tokens  : {', '.join(comp.get('key_tokens', []))}")
    print()
    print("  ─── HTML TEMPLATE ───")
    print(comp.get("html_template", "(no template)"))
    print()
    print("  ─── USAGE NOTES ───")
    for note in comp.get("usage_notes", []):
        print(f"    • {note}")
    print_separator()
    print("STATUS: SUCCESS")


def cmd_token(token_name: str) -> None:
    print_separator(f"Design Sherpa — Token")
    print(f"  Token       : {token_name}")
    print(f"  CSS Var     : --rs-{token_name}")
    print(f"  Include     : design-system/dist/raj-sadan.css")
    print(f"  JSON access : tokens.json → resolve path from token name")
    print()
    print("  ─── QUICK REFERENCE ───")
    # Provide inline hints for common tokens
    hints = {
        "color-primary-500": "#1A3A6B (Royal Indigo — primary brand)",
        "color-secondary-500": "#D4A800 (Sovereign Gold — accent)",
        "space-md": "16px — default component padding",
        "space-sm": "8px — internal component padding",
        "radius-md": "8px — default card/container radius",
        "radius-full": "9999px — avatars, badges, pills",
        "shadow-low": "subtle card shadow at rest",
        "shadow-medium": "hovered card / dropdown shadow",
        "shadow-overlay": "modal / dialog shadow",
    }
    hint = hints.get(token_name)
    if hint:
        print(f"  Value       : {hint}")
    print_separator()
    print("STATUS: SUCCESS")


def cmd_request(request: str, index: dict) -> None:
    model = check_ollama()
    print(f"[Design Sherpa] Processing via {model} ...")
    print()

    # Build context-aware prompt
    comp_summary = json.dumps(
        {k: v.get("description", "") for k, v in index.get("components", {}).items()},
        indent=2
    )
    prompt = f"""Design Request: {request}

Available Components:
{comp_summary}

Provide:
1. RECOMMENDATION — which component(s) best fit this request and why
2. HTML CODE — complete, production-ready HTML using Raj Sadan design system classes
3. NOTES — up to 3 implementation tips (accessibility, tokens, dark mode)"""

    response, success = ollama_generate(prompt, model)

    print_separator("Design Sherpa — Design Guidance")
    print(f"  Request     : {request}")
    print(f"  Model       : {model}")
    print()
    print(response)
    print_separator()
    if success:
        print("STATUS: SUCCESS")
    else:
        print("STATUS: FAILED")
        sys.exit(1)


# ─── Entry Point ───

def run() -> None:
    parser = argparse.ArgumentParser(
        description="Design Sherpa — Raj Sadan UI/UX design guide and code generator"
    )
    parser.add_argument("--list",      action="store_true", help="List all indexed components")
    parser.add_argument("--component", type=str,            help="Get spec + code for a component (e.g. button, card, input)")
    parser.add_argument("--token",     type=str,            help="Look up a design token (e.g. color-primary-500, space-md)")
    parser.add_argument("--request",   type=str,            help="Design request — get guidance + code from LLM")
    parser.add_argument("--dry-run",   action="store_true", help="Validate setup without calling LLM")
    args = parser.parse_args()

    index = load_toml(INDEX_FILE)

    if args.dry_run:
        cmd_dry_run(index)
    elif args.list:
        cmd_list(index)
    elif args.component:
        cmd_component(args.component, index)
    elif args.token:
        cmd_token(args.token)
    elif args.request:
        cmd_request(args.request, index)
    else:
        parser.print_help()


if __name__ == "__main__":
    run()
