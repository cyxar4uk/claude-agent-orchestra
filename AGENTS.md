# AGENTS.md

Guidance for AI coding assistants (and humans) working on this repository.

## What this is

`claude-agent-orchestra`: a small multi-agent orchestrator on the Claude Agent SDK. Python 3.10+, FastAPI, SQLite, pydantic v2. Source in `src/orchestra/`, tests in `tests/`.

## Commands

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest                       # offline; never needs an API key
orchestra run --mock "goal"  # smoke test the CLI
```

## Rules

- Never call the real API in tests. Use `MockBackend` or patch `claude_agent_sdk.query`.
- Keep `src/` domain-agnostic. Domain-specific roles and prompts belong in `examples/`.
- Model IDs are plain strings from settings/YAML. Do not hardcode them outside `settings.py` defaults and docs.
- Read-only by default: new default agents/workflows must not get `Write`, `Edit` or `Bash` unless they are behind `require_approval`.
- Prefer small, reviewable changes; add tests with behavior changes; update `CHANGELOG.md`.
- Never commit `.env`, `*.db`, `data/`, `runs/` or `logs/`.
