# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-25

First public release.

### Added
- Orchestration engine on the Claude Agent SDK: each workflow step runs as one `query()` session with its own system prompt, model, tool allow-list and permission mode.
- Declarative `orchestra.yaml`: agents (inline or file prompts) and workflows. Built-in defaults with `analyst`, `architect`, `implementer`, `reviewer` and `planner` roles.
- Two workflow modes: `pipeline` with `{{ goal }}` / `{{ previous }}` / `{{ steps.<id> }}` templating, and `planner`, where a planner agent proposes a validated JSON plan.
- Human approval gates (`require_approval`) with approve and reject.
- SQLite persistence for runs, steps (model, cost, session id, timings), artifacts and an event log including tool-use audit hooks; artifacts mirrored to `data/runs/<id>/`.
- Knowledge base: Markdown/text files injected into agent prompts.
- Model configuration through env: `claude-sonnet-5-5` for workers by default, optional `claude-opus-5-5` for planners.
- REST API (FastAPI) with optional bearer-token auth: `/health`, `/agents`, `/workflows`, `/knowledge`, `/runs`, `/runs/{id}`, `/events`, `/artifacts`, `/approve`, `/reject`.
- `orchestra` CLI: `run`, `serve`, `agents`, `workflows`, `runs`, `show`, with an offline `--mock` mode.
- Deterministic mock backend for tests and offline demos.
- Docker image and `docker-compose.yml`.
- Examples: `examples/quickstart` and `examples/game-studio`.
- Test suite (no API key required), ruff lint/format, GitHub Actions CI on Python 3.10-3.13.

### Notes
- This project grew out of two private prototypes: a Claude Agent SDK scaffold and a FastAPI orchestration backend. Their domain-specific parts were removed.

[Unreleased]: https://github.com/cyxar4uk/claude-agent-orchestra/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cyxar4uk/claude-agent-orchestra/releases/tag/v0.1.0
