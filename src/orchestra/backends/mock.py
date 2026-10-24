"""Offline deterministic backend: no API key, no network, no cost. Used by tests and ``--mock``."""

from __future__ import annotations

import json
from collections.abc import Callable

from orchestra.backends.base import StepRequest, StepResult

Responder = Callable[[StepRequest], str]


class MockBackend:
    name = "mock"

    def __init__(self, responder: Responder | None = None) -> None:
        self._responder = responder
        self.calls: list[StepRequest] = []

    async def run_step(self, request: StepRequest) -> StepResult:
        self.calls.append(request)
        if self._responder is not None:
            return StepResult(output=self._responder(request), cost_usd=0.0)
        if request.kind == "plan":
            steps = [
                {"agent": name, "task": f"Contribute your part of the goal as the {name}."}
                for name in request.available_agents[:3]
            ]
            return StepResult(output=json.dumps({"steps": steps}), cost_usd=0.0)
        first_line = request.prompt.strip().splitlines()[0][:160] if request.prompt.strip() else ""
        output = (
            f"[mock:{request.agent.name}] {first_line}\n\n"
            "What was done: simulated output.\nWhat remains: nothing.\n"
            "Risks: none (mock backend).\nNext step: run with the real backend."
        )
        return StepResult(output=output, cost_usd=0.0)
