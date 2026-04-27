# Design Sherpa

> Indexed design intelligence — provide component specs, tokens, and production-ready HTML/CSS from the Raj Sadan Design System; fulfill freeform design requests via local LLM

A sherpa from [ai-sherpa](https://github.com/vraj0703/ai-sherpa) — the standard library of execution sherpas for any organization built on [ai-constitution](https://github.com/vraj0703/ai-constitution).

## Authority

- Derives from: Constitution Article IV (Sherpas)
- Invoked by: PM, Mr. V, Ministers
- Reports to: invoker

## Quick start

```bash
python -m ai_sherpa design
# common flags: --list --component <name> --token <name> --request <text> --dry-run
```

## Files

```
design/
├── manifest.toml   # metadata, deps, invocation contract
├── main.py         # entry point
├── __init__.py
├── component-index.toml
```

## Dependencies

| Group | Items |
|---|---|
| Python | — |
| Ollama models | qwen2.5-coder:7b, qwen2.5:14b |
| External | component-index.toml (co-located) |

## Status

`active` — see manifest.toml for full details.

## License

MIT — see the [LICENSE](../../LICENSE) at the repo root.

## See also

- [ai-constitution](https://github.com/vraj0703/ai-constitution) — the governance framework
- [ai-ministers](https://github.com/vraj0703/ai-ministers) — the policy layer that invokes sherpas
- [Sherpa index](../../README.md)
