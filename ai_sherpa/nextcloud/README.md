# Nextcloud Sherpa

> CRUD operations on NextCloud via the Pi API service — file operations, state management, domain data for finance/health/house-ops/career/infrastructure

A sherpa from [ai-sherpa](https://github.com/vraj0703/ai-sherpa) — the standard library of execution sherpas for any organization built on [ai-constitution](https://github.com/vraj0703/ai-constitution).

## Authority

- Derives from: Constitution Article IV (Sherpas)
- Invoked by: PM, Mr. V, Ministers
- Reports to: invoker

## Quick start

```bash
python -m ai_sherpa nextcloud
# common flags: --op <operation> --path <path> --domain <name>
```

## Files

```
nextcloud/
├── manifest.toml   # metadata, deps, invocation contract
├── main.py         # entry point
├── __init__.py
```

## Dependencies

| Group | Items |
|---|---|
| Python | — |
| Ollama models | qwen2.5-coder:7b |
| External | NextCloud API (http://192.168.1.100:3481), gateway/nextcloud.cjs |

## Status

`active` — see manifest.toml for full details.

## License

MIT — see the [LICENSE](../../LICENSE) at the repo root.

## See also

- [ai-constitution](https://github.com/vraj0703/ai-constitution) — the governance framework
- [ai-ministers](https://github.com/vraj0703/ai-ministers) — the policy layer that invokes sherpas
- [Sherpa index](../../README.md)
