"""The orchestration engine: plans, runs steps through a backend, records everything."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from orchestra.backends.base import Backend, StepRequest
from orchestra.knowledge import KnowledgeBase
from orchestra.settings import Settings
from orchestra.spec import AgentSpec, OrchestraConfig, WorkflowSpec, render
from orchestra.store import RunRecord, RunStatus, StepRecord, Store

log = logging.getLogger("orchestra")


class OrchestraError(Exception):
    """Base class for user-facing errors."""


class UnknownWorkflow(OrchestraError):
    pass


class InvalidState(OrchestraError):
    pass


class PlanError(OrchestraError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower() or "step"


def parse_plan(text: str, allowed_agents: list[str], max_steps: int) -> list[dict[str, str]]:
    """Extract ``{"steps": [{"agent", "task"}, ...]}`` from a planner answer and validate it."""
    candidates: list[str] = []
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    candidates.extend(fenced)
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    data: Any = None
    for cand in candidates:
        try:
            data = json.loads(cand.strip())
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        raise PlanError("planner did not return valid JSON")

    steps = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(steps, list) or not steps:
        raise PlanError("plan must contain a non-empty 'steps' list")
    if len(steps) > max_steps:
        steps = steps[:max_steps]

    out: list[dict[str, str]] = []
    for i, item in enumerate(steps, 1):
        if not isinstance(item, dict) or not item.get("agent") or not item.get("task"):
            raise PlanError(f"plan step {i} needs 'agent' and 'task'")
        agent = str(item["agent"])
        if agent not in allowed_agents:
            raise PlanError(f"plan step {i} uses unknown or disallowed agent '{agent}'")
        out.append({"agent": agent, "task": str(item["task"])})
    return out


class Engine:
    def __init__(
        self,
        config: OrchestraConfig,
        settings: Settings,
        store: Store,
        backend: Backend,
    ) -> None:
        self.config = config
        self.settings = settings
        self.store = store
        self.backend = backend
        self.knowledge = KnowledgeBase(config.knowledge_path, settings.knowledge_max_chars)
        self._sem: asyncio.Semaphore | None = None

    # -- public API ------------------------------------------------------------------------
    def create_run(
        self, goal: str, workflow: str | None = None, require_approval: bool | None = None
    ) -> RunRecord:
        name = workflow or self.config.default_workflow
        if not name or name not in self.config.workflows:
            raise UnknownWorkflow(f"unknown workflow: {workflow!r}")
        wf = self.config.workflows[name]
        needs = wf.require_approval if require_approval is None else require_approval
        run = self.store.create_run(goal.strip(), name, needs)
        self.store.add_event(run.id, "run_created", {"workflow": name, "goal": run.goal})
        return run

    async def execute(self, run_id: str) -> RunRecord:
        """Plan (if needed) and run a pending run until it completes or pauses for approval."""
        run = self._require(run_id, {RunStatus.pending})
        wf = self.config.workflows[run.workflow]
        async with self._semaphore():
            try:
                if wf.mode == "planner":
                    await self._plan(run, wf)
                else:
                    for i, spec in enumerate(wf.steps, 1):
                        self.store.add_step(run.id, i, spec.id, "task", spec.agent, spec.task)
                if run.require_approval:
                    self.store.set_run_status(run.id, RunStatus.awaiting_approval)
                    self.store.add_event(
                        run.id,
                        "awaiting_approval",
                        {"steps": [s.key for s in self.store.list_steps(run.id)]},
                    )
                else:
                    await self._run_steps(run, wf)
            except Exception as exc:  # noqa: BLE001 - any failure must end the run cleanly
                self._fail(run.id, exc)
        return self._require(run_id)

    async def approve(self, run_id: str) -> RunRecord:
        run = self._require(run_id, {RunStatus.awaiting_approval})
        wf = self.config.workflows[run.workflow]
        self.store.add_event(run.id, "approved", {})
        async with self._semaphore():
            try:
                await self._run_steps(run, wf)
            except Exception as exc:  # noqa: BLE001
                self._fail(run.id, exc)
        return self._require(run_id)

    def reject(self, run_id: str, reason: str = "") -> RunRecord:
        run = self._require(run_id, {RunStatus.awaiting_approval})
        self.store.set_run_status(run.id, RunStatus.rejected, reason or "rejected by reviewer")
        self.store.add_event(run.id, "rejected", {"reason": reason})
        return self._require(run_id)

    # -- internals -------------------------------------------------------------------------
    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, self.settings.max_concurrent_runs))
        return self._sem

    def _require(self, run_id: str, allowed: set[RunStatus] | None = None) -> RunRecord:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if allowed is not None and run.status not in allowed:
            names = ", ".join(sorted(s.value for s in allowed))
            raise InvalidState(f"run is '{run.status.value}', expected: {names}")
        return run

    def _fail(self, run_id: str, exc: Exception) -> None:
        log.warning("run %s failed: %s", run_id, exc)
        self.store.set_run_status(run_id, RunStatus.failed, str(exc))
        self.store.add_event(run_id, "run_failed", {"error": str(exc)})

    def _emitter(self, run_id: str):
        def emit(type_: str, data: dict[str, Any]) -> None:
            self.store.add_event(run_id, type_, data)

        return emit

    def _system_prompt(self, agent: AgentSpec) -> str:
        return "\n\n".join(p for p in (agent.prompt.strip(), self.config.shared_rules.strip()) if p)

    def _request(
        self, run: RunRecord, step_key: str, kind: str, agent: AgentSpec, prompt: str, **extra: Any
    ) -> StepRequest:
        s = self.settings
        permission_mode = agent.permission_mode or s.permission_mode
        return StepRequest(
            run_id=run.id,
            step_key=step_key,
            kind=kind,
            agent=agent,
            model=s.resolve_model(agent.model),
            system_prompt=self._system_prompt(agent),
            prompt=prompt,
            cwd=s.workspace_path,
            permission_mode=permission_mode,
            max_turns=agent.max_turns or s.max_turns,
            max_budget_usd=s.max_budget_usd,
            load_project_settings=s.load_project_settings,
            emit=self._emitter(run.id),
            **extra,
        )

    async def _plan(self, run: RunRecord, wf: WorkflowSpec) -> None:
        assert wf.planner
        planner = self.config.agents[wf.planner]
        allowed = wf.agents or [n for n in self.config.agents if n != wf.planner]
        self.store.set_run_status(run.id, RunStatus.planning)
        self.store.add_event(run.id, "planning_started", {"planner": wf.planner})

        roster = "\n".join(f"- {n}: {self.config.agents[n].description}" for n in allowed)
        prompt = (
            f"Goal:\n{run.goal}\n\n"
            f"Available agents:\n{roster}\n\n"
            f"Break the goal into at most {wf.max_steps} ordered steps, each handled by exactly one "
            "of the available agents. Reply with ONLY a JSON object, no prose:\n"
            '{"steps": [{"agent": "<agent name>", "task": "<concrete instruction>"}]}'
        )
        if planner.use_knowledge and (ctx := self.knowledge.render()):
            prompt += f"\n\n## Project knowledge\n{ctx}"

        step = self.store.add_step(run.id, 0, "plan", "plan", planner.name, prompt)
        request = self._request(run, "plan", "plan", planner, prompt, available_agents=allowed)
        result = await self._run_one(run, step, request)

        plan = parse_plan(result.output, allowed, wf.max_steps)
        for i, item in enumerate(plan, 1):
            self.store.add_step(
                run.id, i, f"step-{i}-{_slug(item['agent'])}", "task", item["agent"], item["task"]
            )
        self.store.add_artifact(
            run.id,
            "plan",
            "Plan",
            json.dumps({"steps": plan}, indent=2, ensure_ascii=False),
            step.id,
        )
        self.store.add_event(run.id, "plan_created", {"steps": plan})

    async def _run_one(self, run: RunRecord, step: StepRecord, request: StepRequest):
        self.store.start_step(step.id, request.model)
        self.store.add_event(
            run.id, "step_started", {"step": step.key, "agent": step.agent, "model": request.model}
        )
        try:
            result = await self.backend.run_step(request)
        except Exception as exc:
            self.store.finish_step(step.id, error=str(exc))
            self.store.add_event(run.id, "step_failed", {"step": step.key, "error": str(exc)})
            raise
        self.store.finish_step(
            step.id, output=result.output, cost_usd=result.cost_usd, session_id=result.session_id
        )
        self.store.add_event(
            run.id,
            "step_completed",
            {
                "step": step.key,
                "agent": step.agent,
                "cost_usd": result.cost_usd,
                "chars": len(result.output),
            },
        )
        return result

    async def _run_steps(self, run: RunRecord, wf: WorkflowSpec) -> None:
        self.store.set_run_status(run.id, RunStatus.running)
        self.store.add_event(run.id, "run_started", {})
        steps = [s for s in self.store.list_steps(run.id) if s.kind == "task"]
        outputs: dict[str, str] = {}
        previous = ""

        for step in steps:
            agent = self.config.agents[step.agent]
            variables = {"goal": run.goal, "previous": previous}
            variables.update({f"steps.{k}": v for k, v in outputs.items()})
            prompt = render(step.task, variables)
            if wf.mode == "planner":
                prompt = f"## Overall goal\n{run.goal}\n\n## Your task\n{prompt}"
                if previous:
                    prompt += f"\n\n## Output of the previous step\n{previous}"
            if agent.use_knowledge and (ctx := self.knowledge.render()):
                prompt += f"\n\n## Project knowledge\n{ctx}"

            request = self._request(run, step.key, "task", agent, prompt)
            result = await self._run_one(run, step, request)
            outputs[step.key] = result.output
            previous = result.output[-self.settings.context_max_chars :]
            self.store.add_artifact(
                run.id, "step_output", f"{step.key} ({step.agent})", result.output, step.id
            )

        report = self._aggregate(run, steps, outputs)
        self.store.add_artifact(run.id, "result", "Aggregated result", report)
        self._write_files(run, report, steps, outputs)
        self.store.set_run_status(run.id, RunStatus.completed)
        self.store.add_event(run.id, "run_completed", {"steps": len(steps)})

    @staticmethod
    def _aggregate(run: RunRecord, steps: list[StepRecord], outputs: dict[str, str]) -> str:
        parts = [f"# Result\n\n**Goal:** {run.goal}\n"]
        for step in steps:
            parts.append(f"## {step.key} ({step.agent})\n\n{outputs.get(step.key, '').strip()}\n")
        return "\n".join(parts)

    def _write_files(
        self, run: RunRecord, report: str, steps: list[StepRecord], outputs: dict[str, str]
    ) -> None:
        """Mirror artifacts to ``<data_dir>/runs/<run_id>/`` so they are greppable on disk."""
        try:
            folder: Path = self.settings.data_path / "runs" / run.id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "result.md").write_text(report, encoding="utf-8")
            for step in steps:
                name = f"{step.idx:02d}-{_slug(step.agent)}.md"
                (folder / name).write_text(outputs.get(step.key, ""), encoding="utf-8")
        except OSError as exc:  # artifacts are still in SQLite; do not fail the run
            log.warning("could not write artifact files: %s", exc)
