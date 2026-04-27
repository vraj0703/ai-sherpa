# Boot Sherpa

> Initialize Raj Sadan — start Ollama, Pi SSH, services, WhatsApp, cron, scrum, memory summary, build cloud LLM prompt, hand over to Mr. V

A sherpa from [ai-sherpa](https://github.com/vraj0703/ai-sherpa) — the standard library of execution sherpas for any organization built on [ai-constitution](https://github.com/vraj0703/ai-constitution).

## Authority

- Derives from: Constitution Article IV (Sherpas)
- Invoked by: PM, Mr. V, Ministers
- Reports to: invoker

## Quick start

```bash
python -m ai_sherpa boot
# common flags: --dry-run --no-whatsapp --no-cron --verbose
```

## Files

```
boot/
├── manifest.toml   # metadata, deps, invocation contract
├── main.py         # entry point
├── __init__.py
```

## Dependencies

| Group | Items |
|---|---|
| Python | — |
| Ollama models | llama3.2:3b, phi3:latest |
| External | Ollama, Raspberry Pi SSH, WhatsApp gateway, Cron service, Claude Code CLI |

## Status

`active` — see manifest.toml for full details.

## License

MIT — see the [LICENSE](../../LICENSE) at the repo root.

## See also

- [ai-constitution](https://github.com/vraj0703/ai-constitution) — the governance framework
- [ai-ministers](https://github.com/vraj0703/ai-ministers) — the policy layer that invokes sherpas
- [Sherpa index](../../README.md)
