"""FastAPI application: REST API for agents, workflows, runs, artifacts and approvals."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from orchestra import __version__
from orchestra.backends import create_backend
from orchestra.engine import Engine, InvalidState, UnknownWorkflow
from orchestra.settings import Settings, get_settings
from orchestra.spec import OrchestraConfig, load_config
from orchestra.store import ArtifactRecord, EventRecord, RunRecord, StepRecord, Store

log = logging.getLogger("orchestra")


class CreateRun(BaseModel):
    goal: str = Field(min_length=3, max_length=20_000)
    workflow: str | None = None
    require_approval: bool | None = None


class RejectRun(BaseModel):
    reason: str = ""


class RunDetail(RunRecord):
    cost_usd: float
    steps: list[StepRecord]


def build_engine(settings: Settings, config: OrchestraConfig | None = None) -> Engine:
    config = config or load_config(settings.config or None)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    return Engine(config, settings, Store(settings.db_path), create_backend(settings))


def create_app(engine: Engine | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or (engine.settings if engine else get_settings())
    logging.basicConfig(level=settings.log_level.upper())
    tasks: set[asyncio.Task[Any]] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = engine or build_engine(settings)
        app.state.engine = eng
        interrupted = eng.store.fail_interrupted_runs()
        if interrupted:
            log.warning("marked %d interrupted run(s) as failed", interrupted)
        yield
        for task in list(tasks):
            task.cancel()

    app = FastAPI(
        title="claude-agent-orchestra",
        version=__version__,
        description="Multi-agent orchestration on the Claude Agent SDK.",
        lifespan=lifespan,
    )
    if engine is not None:  # make state available even before lifespan runs (e.g. plain imports)
        app.state.engine = engine

    def get_engine() -> Engine:
        return app.state.engine

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        if not settings.api_token:
            return
        expected = f"Bearer {settings.api_token}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token")

    protected = [Depends(require_auth)]

    def spawn(coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def detail(eng: Engine, run: RunRecord) -> RunDetail:
        steps = eng.store.list_steps(run.id)
        cost = sum(s.cost_usd or 0.0 for s in steps)
        return RunDetail(**run.model_dump(), cost_usd=round(cost, 6), steps=steps)

    def get_run_or_404(eng: Engine, run_id: str) -> RunRecord:
        run = eng.store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "backend": settings.backend}

    @app.get("/agents", dependencies=protected)
    def list_agents(eng: Engine = Depends(get_engine)) -> list[dict[str, Any]]:
        return [
            {
                "name": a.name,
                "description": a.description,
                "tools": a.tools,
                "model": settings.resolve_model(a.model),
            }
            for a in eng.config.agents.values()
        ]

    @app.get("/workflows", dependencies=protected)
    def list_workflows(eng: Engine = Depends(get_engine)) -> list[dict[str, Any]]:
        return [
            {
                "name": w.name,
                "description": w.description,
                "mode": w.mode,
                "require_approval": w.require_approval,
                "default": w.name == eng.config.default_workflow,
                "steps": [{"id": s.id, "agent": s.agent} for s in w.steps],
            }
            for w in eng.config.workflows.values()
        ]

    @app.get("/knowledge", dependencies=protected)
    def list_knowledge(eng: Engine = Depends(get_engine)) -> list[dict[str, object]]:
        return eng.knowledge.listing()

    @app.post("/runs", dependencies=protected, status_code=status.HTTP_202_ACCEPTED)
    async def create_run(
        body: CreateRun,
        wait: bool = Query(False, description="Block until the run finishes or needs approval."),
        eng: Engine = Depends(get_engine),
    ) -> RunDetail:
        try:
            run = eng.create_run(body.goal, body.workflow, body.require_approval)
        except UnknownWorkflow as exc:
            raise HTTPException(404, str(exc)) from exc
        if wait:
            run = await eng.execute(run.id)
        else:
            spawn(eng.execute(run.id))
        return detail(eng, run)

    @app.get("/runs", dependencies=protected)
    def list_runs(
        limit: int = Query(50, ge=1, le=500),
        run_status: str | None = Query(None, alias="status"),
        eng: Engine = Depends(get_engine),
    ) -> list[RunRecord]:
        return eng.store.list_runs(limit, run_status)

    @app.get("/runs/{run_id}", dependencies=protected)
    def get_run(run_id: str, eng: Engine = Depends(get_engine)) -> RunDetail:
        return detail(eng, get_run_or_404(eng, run_id))

    @app.get("/runs/{run_id}/events", dependencies=protected)
    def run_events(
        run_id: str, after_id: int = 0, eng: Engine = Depends(get_engine)
    ) -> list[EventRecord]:
        get_run_or_404(eng, run_id)
        return eng.store.list_events(run_id, after_id)

    @app.get("/runs/{run_id}/artifacts", dependencies=protected)
    def run_artifacts(run_id: str, eng: Engine = Depends(get_engine)) -> list[ArtifactRecord]:
        get_run_or_404(eng, run_id)
        return eng.store.list_artifacts(run_id)

    @app.post("/runs/{run_id}/approve", dependencies=protected, status_code=202)
    async def approve_run(
        run_id: str, wait: bool = False, eng: Engine = Depends(get_engine)
    ) -> RunDetail:
        run = get_run_or_404(eng, run_id)
        if run.status.value != "awaiting_approval":
            raise HTTPException(409, f"run is '{run.status.value}', not awaiting approval")
        if wait:
            try:
                run = await eng.approve(run_id)
            except InvalidState as exc:
                raise HTTPException(409, str(exc)) from exc
        else:
            spawn(eng.approve(run_id))
        return detail(eng, run)

    @app.post("/runs/{run_id}/reject", dependencies=protected)
    def reject_run(
        run_id: str, body: RejectRun | None = None, eng: Engine = Depends(get_engine)
    ) -> RunDetail:
        get_run_or_404(eng, run_id)
        try:
            run = eng.reject(run_id, body.reason if body else "")
        except InvalidState as exc:
            raise HTTPException(409, str(exc)) from exc
        return detail(eng, run)

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn orchestra.api:app_factory --factory``."""
    return create_app()
