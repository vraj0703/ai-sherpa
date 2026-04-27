# Changelog

## [Unreleased]

### Added (M3 — RAJ-61 + RAJ-62 + RAJ-63 + RAJ-64 + RAJ-65)

- **`ai_sherpa/onboarding/`** — interactive onboarding sherpa.
  - `flow.py` — eight-section Q&A flow (org, pm, agent, ministries, sherpas, protocols, llm, services). Humble per-section WHY framing per PM directive (RAJ-43). Two prompters: `InteractivePrompter` (questionary) and `ScriptedPrompter` (TOML fixture for tests + automation).
  - `validation.py` — required-field enforcement (org.name, org.pm.name, org.agent.name reject placeholders), slug auto-derivation, `agent.reports_to` auto-fill from `pm.title`, kebab-case slug format check.
  - `main.py` — CLI dispatcher with `run` (interactive Q&A) and `sync` (re-render after framework upgrade) subcommands. `--answers <fixture>` for non-interactive runs. `--dry-run` previews without writing. `--constitution-dir` overrides where to find templates (for tests). Reconfigures stdout to UTF-8 on Windows so emoji output doesn't crash the cp1252 console.
  - `manifest.toml` — registers onboarding as a sherpa with `python -m ai_sherpa onboarding [args]` invocation.
- **Optional opt-ins** (RAJ-63): mind, memory, senses, knowledge, dashboard surfaced as a checkbox question with one-sentence WHY each. Selected opt-ins prompt for the host URL and write to `org.services.<name>`.
- **Sync subcommand** (RAJ-64): reads existing org-config, detects schema drift against new defaults (variables added/removed), accepts framework defaults for new keys, drops removed keys, re-renders.
- **`tests/fixtures/raj-sadan-answers.toml`** — scripted answers fixture mirroring raj-sadan's actual values for the e2e test.
- **`tests/test_onboarding_e2e.py`** (RAJ-65) — proves end-to-end that scripted onboarding produces a bundle containing raj-sadan's substituted values, the bundle has all 24 expected files, no leftover template syntax, the .onboarding-log.json captures the Q&A, --dry-run does not write, and sync is idempotent against an unchanged config.
- **19 new tests** (4 e2e + 4 flow + 11 validation), all green on Python 3.13.7.

## [0.1.0] — 2026-04-27

First public release. Closes M4 (RAJ-60 + RAJ-68 + RAJ-69 + RAJ-70).

### Added

- Initial repository scaffold (RAJ-60): collection README, MIT, `pyproject.toml`, CI matrix on Python 3.10/3.11/3.12, `ai_sherpa/` Python package with CLI dispatcher.
- 7 sherpas lifted from raj-sadan (RAJ-68): boot, exit, scrum, design, nextcloud, crawler, scaffold. Each carries its `manifest.toml` + `main.py` + any auxiliary files (component-index, requirements). Per-sherpa READMEs generated from the M1 audit template.
- CLI dispatcher (RAJ-69): `python -m ai_sherpa <name>` runs a sherpa, `python -m ai_sherpa scaffold <new-name>` generates a new sherpa from the template.
- Smoke test (RAJ-70): `tests/test_dispatcher.py` proves the registry loads each sherpa's manifest, the CLI lists all sherpas, and `scaffold` refuses to overwrite existing directories.

### Known limitation

- v0.0.1 sherpas reference raj-sadan filesystem paths (memory/journal, gateway adapters). They run correctly when consumed as a submodule from a host repo whose layout matches raj-sadan's. Full portability refactor is a follow-up to RAJ-70.
