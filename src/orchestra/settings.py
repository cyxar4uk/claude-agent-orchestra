"""Runtime settings (environment variables / .env). Everything is prefixed ``ORCHESTRA_``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    backend: Literal["claude", "mock"] = "claude"

    # Model IDs are plain strings so new models need no code change.
    worker_model: str = "claude-sonnet-5-5"
    planner_model: str = ""  # empty -> falls back to worker_model

    config: str = ""  # path to orchestra.yaml; empty -> built-in defaults
    workspace: str = "."  # directory the agents work in
    data_dir: str = "data"  # SQLite db + run artifacts

    permission_mode: str = "acceptEdits"
    max_turns: int | None = 30
    max_budget_usd: float | None = None
    load_project_settings: bool = True  # let the SDK read CLAUDE.md / .claude/ from the workspace
    max_concurrent_runs: int = 2

    knowledge_max_chars: int = 40_000
    context_max_chars: int = 24_000  # cap for "previous step outputs" passed to the next step

    host: str = "127.0.0.1"
    port: int = 8000
    api_token: str = ""
    log_level: str = "INFO"

    @property
    def resolved_planner_model(self) -> str:
        return self.planner_model.strip() or self.worker_model

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace).expanduser().resolve()

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()

    @property
    def db_path(self) -> Path:
        return self.data_path / "orchestra.db"

    def resolve_model(self, alias_or_id: str) -> str:
        """Map the aliases ``worker`` / ``planner`` to configured IDs; pass other values through."""
        value = (alias_or_id or "worker").strip()
        if value == "planner":
            return self.resolved_planner_model
        if value == "worker":
            return self.worker_model
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Export .env into os.environ so ANTHROPIC_API_KEY reaches the Claude Agent SDK subprocess.
    load_dotenv(override=False)
    return Settings()
