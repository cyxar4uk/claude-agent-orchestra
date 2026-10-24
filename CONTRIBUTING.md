# Contributing

Thanks for helping. This project aims to stay small, so please favor focused changes.

## Setup

```bash
git clone https://github.com/cyxar4uk/claude-agent-orchestra.git
cd claude-agent-orchestra
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before you open a pull request

```bash
ruff check .
ruff format --check .      # or `ruff format .` to fix
pytest
```

CI runs exactly these on Python 3.10 to 3.13. Tests must not call the real API: use `MockBackend` (see `tests/conftest.py`) or patch `claude_agent_sdk.query` (see `tests/test_claude_backend.py`). No API key is needed to develop or test.

## Guidelines

- One logical change per PR; describe the problem it solves, not just the diff.
- Add or update tests for behavior changes. Bug fixes should include a failing-then-passing test.
- Keep the core generic: no project-specific names, prompts, paths or domains in `src/`. Domain examples belong in `examples/`.
- New configuration goes in `Settings` (env, prefix `ORCHESTRA_`) or `orchestra.yaml`, and must be documented in `.env.example` and the README.
- Update `CHANGELOG.md` under "Unreleased".
- Never commit `.env`, databases, run artifacts or logs. `.gitignore` covers them; double-check `git status`.

## Project layout

```
src/orchestra/
  spec.py        agents / workflows config models and loader
  engine.py      plan -> approve -> execute, step orchestration
  backends/      Backend protocol, Claude SDK backend, mock backend
  store.py       SQLite persistence
  knowledge.py   knowledge-base loader
  api.py         FastAPI app          cli.py   command line
  defaults/      built-in agents, prompts, workflows
examples/        sample configs
tests/           pytest suite (offline)
```

## Reporting bugs and ideas

Use the issue templates. For security problems, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

By contributing you agree that your contributions are licensed under the MIT License.
