"""Backend interface. A backend executes ONE agent step and returns its text output."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from orchestra.spec import AgentSpec

Emit = Callable[[str, dict[str, Any]], None]


class BackendError(RuntimeError):
    """The backend could not complete the step (API error, CLI missing, ...)."""


@dataclass
class StepRequest:
    run_id: str
    step_key: str
    kind: str  # "plan" | "task"
    agent: AgentSpec
    model: str  # already resolved to a concrete model ID
    system_prompt: str
    prompt: str
    cwd: Path
    permission_mode: str
    max_turns: int | None = None
    max_budget_usd: float | None = None
    load_project_settings: bool = True
    available_agents: list[str] = field(default_factory=list)  # plan steps only
    emit: Emit = field(default=lambda _type, _data: None)


@dataclass
class StepResult:
    output: str
    cost_usd: float | None = None
    session_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class Backend(Protocol):
    name: str

    async def run_step(self, request: StepRequest) -> StepResult: ...
