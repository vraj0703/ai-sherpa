# ai-sherpa

> Execution sherpas for AI agents built on [ai-constitution](https://github.com/vraj0703/ai-constitution).

Where ministers set policy, sherpas do the work. Each sherpa is a Python module that wraps a concrete operation: starting services, shutting down cleanly, reading plans, indexing design data, talking to NextCloud, crawling APIs, walking a new user through onboarding.

## Sherpas

| Sherpa | Purpose | Mandatory? |
|---|---|---|
| **boot** | Initialize the agent stack — start services, build prompt, hand to the agent | yes |
| **exit** | Mechanical shutdown — set session flag, stop services in parallel, save checkpoint | yes |
| **onboarding** | Walk a new org through the variable registry; render templates | yes (populated in M3) |
| **scrum** | Read active plans, evaluate dependencies, alert on blockers | optional |
| **design** | Indexed design intelligence — component specs, tokens, freeform requests | optional |
| **nextcloud** | CRUD on NextCloud via the Pi API service | optional |
| **crawler** | Discover APIs/tools/services from curated sources, classify, dedup, import to Knowledge | optional |
| **scaffold** | Sherpa generator template — `python -m ai_sherpa scaffold <new-name>` | n/a (tooling) |

## CLI

```bash
python -m ai_sherpa                       # list available sherpas
python -m ai_sherpa <name> [args...]      # run a sherpa
python -m ai_sherpa scaffold <new-name>   # generate a new sherpa from the template
```

## Honest portability disclaimer (v0.0.1)

Most sherpas in this v0.0.1 release were lifted from raj-sadan and still reference raj-sadan's specific filesystem layout (e.g. `<repo-root>/memory/journal/`, `gateway/*.cjs` adapters). They run correctly **when consumed as a git submodule from a host repo whose layout matches raj-sadan's** — that's the intended consumer path right now.

A future v0.2.0 refactor will inject paths via configuration so every sherpa is fully portable in isolation. Tracked as a follow-up to RAJ-70.

## Status

v0.0.1 — populated by Linear issues RAJ-60, RAJ-68, RAJ-69, RAJ-70. Onboarding sherpa is M3 (RAJ-61). Public release is M5 (RAJ-75).

## See also

- [ai-constitution](https://github.com/vraj0703/ai-constitution)
- [ai-ministers](https://github.com/vraj0703/ai-ministers)

## License

MIT.
