# Changelog

## [Unreleased]

### Added

- Initial repository scaffold (RAJ-60): collection README, MIT, `pyproject.toml`, CI matrix on Python 3.10/3.11/3.12, `ai_sherpa/` Python package with CLI dispatcher.
- 7 sherpas lifted from raj-sadan (RAJ-68): boot, exit, scrum, design, nextcloud, crawler, scaffold. Each carries its `manifest.toml` + `main.py` + any auxiliary files (component-index, requirements). Per-sherpa READMEs generated from the M1 audit template.
- CLI dispatcher (RAJ-69): `python -m ai_sherpa <name>` runs a sherpa, `python -m ai_sherpa scaffold <new-name>` generates a new sherpa from the template.
- Smoke test (RAJ-70): `tests/test_dispatcher.py` proves the registry loads each sherpa's manifest, the CLI lists all sherpas, and `scaffold` refuses to overwrite existing directories.

### Known limitation

- v0.0.1 sherpas reference raj-sadan filesystem paths (memory/journal, gateway adapters). They run correctly when consumed as a submodule from a host repo whose layout matches raj-sadan's. Full portability refactor is a follow-up to RAJ-70.
