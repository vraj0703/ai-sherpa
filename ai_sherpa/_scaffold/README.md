# -Scaffold Sherpa

> <one-sentence description of what this sherpa does>

A sherpa from [ai-sherpa](https://github.com/vraj0703/ai-sherpa) — the standard library of execution sherpas for any organization built on [ai-constitution](https://github.com/vraj0703/ai-constitution).

## Authority

- Derives from: Constitution Article IV (Sherpas)
- Invoked by: PM, Mr. V
- Reports to: invoker

## Quick start

```bash
python -m ai_sherpa scaffold <new-name>
# common flags: --dry-run --verbose
```

## Files

```
_scaffold/
├── manifest.toml   # metadata, deps, invocation contract
├── main.py         # entry point
├── __init__.py
```

## Dependencies

| Group | Items |
|---|---|
| Python | — |
| Ollama models | — |
| External | Nothing |

## Status

`draft` — see manifest.toml for full details.

## License

MIT — see the [LICENSE](../../LICENSE) at the repo root.

## See also

- [ai-constitution](https://github.com/vraj0703/ai-constitution) — the governance framework
- [ai-ministers](https://github.com/vraj0703/ai-ministers) — the policy layer that invokes sherpas
- [Sherpa index](../../README.md)
