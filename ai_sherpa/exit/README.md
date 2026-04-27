# Exit Sherpa

> Mechanical shutdown of Raj Sadan — set session flag, stop Cortex, notify, stop services in parallel, save checkpoint, verify, validate cognitive state

A sherpa from [ai-sherpa](https://github.com/vraj0703/ai-sherpa) — the standard library of execution sherpas for any organization built on [ai-constitution](https://github.com/vraj0703/ai-constitution).

## Authority

- Derives from: Constitution Article IV (Sherpas)
- Invoked by: PM, Mr. V, Ministers
- Reports to: invoker

## Quick start

```bash
python -m ai_sherpa exit
# common flags: --dry-run --no-whatsapp --no-memory --verbose --memo
```

## Files

```
exit/
├── manifest.toml   # metadata, deps, invocation contract
├── main.py         # entry point
├── __init__.py
```

## Dependencies

| Group | Items |
|---|---|
| Python | — |
| Ollama models | — |
| External | Cortex, WhatsApp gateway, NextCloud (checkpoint), Senses, Knowledge, Dashboard, Cron |

## Status

`active` — see manifest.toml for full details.

## License

MIT — see the [LICENSE](../../LICENSE) at the repo root.

## See also

- [ai-constitution](https://github.com/vraj0703/ai-constitution) — the governance framework
- [ai-ministers](https://github.com/vraj0703/ai-ministers) — the policy layer that invokes sherpas
- [Sherpa index](../../README.md)
