# Crawler Sherpa

> Discover new APIs, tools, and services from curated web sources (public-apis, free-for-dev, Exa) and import into Knowledge Service after LLM classification and dedup

A sherpa from [ai-sherpa](https://github.com/vraj0703/ai-sherpa) — the standard library of execution sherpas for any organization built on [ai-constitution](https://github.com/vraj0703/ai-constitution).

## Authority

- Derives from: Constitution Article IV (Sherpas)
- Invoked by: PM, Mr. V, Cron
- Reports to: invoker

## Quick start

```bash
python -m ai_sherpa crawler
# common flags: --discover --source <url> --report --dry-run --verbose
```

## Files

```
crawler/
├── manifest.toml   # metadata, deps, invocation contract
├── main.py         # entry point
├── __init__.py
├── requirements.txt
```

## Dependencies

| Group | Items |
|---|---|
| Python | — |
| Ollama models | qwen2.5-coder:7b, llama3.2:3b |
| External | Knowledge Service (http://127.0.0.1:3484), GitHub API, Exa API (EXA_API_KEY) |

## Status

`active` — see manifest.toml for full details.

## License

MIT — see the [LICENSE](../../LICENSE) at the repo root.

## See also

- [ai-constitution](https://github.com/vraj0703/ai-constitution) — the governance framework
- [ai-ministers](https://github.com/vraj0703/ai-ministers) — the policy layer that invokes sherpas
- [Sherpa index](../../README.md)
