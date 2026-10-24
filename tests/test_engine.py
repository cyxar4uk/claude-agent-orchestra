import json

import pytest

from orchestra.backends import MockBackend
from orchestra.backends.base import BackendError
from orchestra.engine import InvalidState, PlanError, UnknownWorkflow, parse_plan
from orchestra.store import RunStatus, StepStatus


async def test_pipeline_runs_all_steps_in_order(engine, backend):
    run = engine.create_run("Add a login page")
    run = await engine.execute(run.id)

    assert run.status == RunStatus.completed
    steps = engine.store.list_steps(run.id)
    assert [s.agent for s in steps] == ["analyst", "architect", "reviewer"]
    assert all(s.status == StepStatus.completed for s in steps)
    kinds = [a.kind for a in engine.store.list_artifacts(run.id)]
    assert kinds.count("step_output") == 3 and "result" in kinds


async def test_previous_outputs_flow_into_next_prompt(config, settings):
    seen: list[str] = []

    def responder(req):
        seen.append(req.prompt)
        return f"OUTPUT-OF-{req.agent.name.upper()}"

    from orchestra.engine import Engine
    from orchestra.store import Store

    eng = Engine(config, settings, Store(":memory:"), MockBackend(responder))
    run = eng.create_run("goal text")
    await eng.execute(run.id)

    assert "goal text" in seen[0]
    assert "OUTPUT-OF-ANALYST" in seen[1]  # {{ steps.requirements }}
    assert "OUTPUT-OF-ARCHITECT" in seen[2]  # {{ steps.design }}


async def test_models_and_tools_reach_backend(config, settings):
    from orchestra.engine import Engine
    from orchestra.store import Store

    settings = settings.model_copy(update={"planner_model": "claude-opus-5-5"})
    backend = MockBackend()
    eng = Engine(config, settings, Store(":memory:"), backend)
    await eng.execute(eng.create_run("x", workflow="auto", require_approval=False).id)

    plan_call = backend.calls[0]
    assert plan_call.kind == "plan" and plan_call.model == "claude-opus-5-5"
    assert all(c.model == "claude-sonnet-5-5" for c in backend.calls[1:])
    assert plan_call.cwd == settings.workspace_path


async def test_approval_gate_pauses_then_resumes(engine, backend):
    run = engine.create_run("Refactor module", workflow="implement")
    assert run.require_approval is True
    run = await engine.execute(run.id)

    assert run.status == RunStatus.awaiting_approval
    assert backend.calls == []  # nothing ran yet
    assert [s.status for s in engine.store.list_steps(run.id)] == [StepStatus.pending] * 3

    run = await engine.approve(run.id)
    assert run.status == RunStatus.completed
    assert len(backend.calls) == 3


async def test_reject_stops_the_run(engine, backend):
    run = await engine.execute(engine.create_run("Do risky thing", workflow="implement").id)
    run = engine.reject(run.id, "too risky")
    assert run.status == RunStatus.rejected and run.error == "too risky"
    with pytest.raises(InvalidState):
        await engine.approve(run.id)
    assert backend.calls == []


async def test_cannot_execute_twice(engine):
    run = engine.create_run("goal")
    await engine.execute(run.id)
    with pytest.raises(InvalidState):
        await engine.execute(run.id)


async def test_step_failure_fails_run_and_stops(config, settings):
    from orchestra.engine import Engine
    from orchestra.store import Store

    def boom(req):
        if req.agent.name == "architect":
            raise BackendError("api down")
        return "ok"

    eng = Engine(config, settings, Store(":memory:"), MockBackend(boom))
    run = await eng.execute(eng.create_run("goal").id)

    assert run.status == RunStatus.failed and "api down" in run.error
    statuses = [s.status for s in eng.store.list_steps(run.id)]
    assert statuses == [StepStatus.completed, StepStatus.failed, StepStatus.pending]
    assert "run_failed" in [e.type for e in eng.store.list_events(run.id)]


async def test_planner_mode_builds_steps_from_plan(config, settings):
    from orchestra.engine import Engine
    from orchestra.store import Store

    plan = {
        "steps": [
            {"agent": "architect", "task": "design it"},
            {"agent": "reviewer", "task": "check it"},
        ]
    }

    def responder(req):
        return "```json\n" + json.dumps(plan) + "\n```" if req.kind == "plan" else "done"

    eng = Engine(config, settings, Store(":memory:"), MockBackend(responder))
    run = eng.create_run("big goal", workflow="auto", require_approval=False)
    run = await eng.execute(run.id)

    assert run.status == RunStatus.completed
    steps = eng.store.list_steps(run.id)
    assert [(s.kind, s.agent) for s in steps] == [
        ("plan", "planner"),
        ("task", "architect"),
        ("task", "reviewer"),
    ]


async def test_planner_mode_rejects_unknown_agent(config, settings):
    from orchestra.engine import Engine
    from orchestra.store import Store

    bad = json.dumps({"steps": [{"agent": "wizard", "task": "do magic"}]})
    eng = Engine(config, settings, Store(":memory:"), MockBackend(lambda r: bad))
    run = await eng.execute(eng.create_run("g", workflow="auto", require_approval=False).id)
    assert run.status == RunStatus.failed and "wizard" in run.error


async def test_planner_with_approval_shows_plan_first(engine, backend):
    run = engine.create_run("g", workflow="auto")  # auto requires approval by default
    run = await engine.execute(run.id)
    assert run.status == RunStatus.awaiting_approval
    assert len(backend.calls) == 1  # only the planner ran
    assert [s.kind for s in engine.store.list_steps(run.id)][0] == "plan"
    run = await engine.approve(run.id)
    assert run.status == RunStatus.completed


async def test_artifact_files_are_written(engine, settings):
    run = await engine.execute(engine.create_run("goal").id)
    folder = settings.data_path / "runs" / run.id
    assert (folder / "result.md").read_text(encoding="utf-8").startswith("# Result")


def test_unknown_workflow(engine):
    with pytest.raises(UnknownWorkflow):
        engine.create_run("goal", workflow="nope")


@pytest.mark.parametrize(
    "text",
    [
        '{"steps":[{"agent":"a","task":"t"}]}',
        '[{"agent":"a","task":"t"}]',
        'Here you go:\n```json\n{"steps":[{"agent":"a","task":"t"}]}\n```',
        'Sure! {"steps":[{"agent":"a","task":"t"}]} Hope it helps',
    ],
)
def test_parse_plan_accepts_common_shapes(text):
    assert parse_plan(text, ["a"], 5) == [{"agent": "a", "task": "t"}]


def test_parse_plan_errors_and_truncation():
    with pytest.raises(PlanError):
        parse_plan("no json here", ["a"], 5)
    with pytest.raises(PlanError):
        parse_plan('{"steps": []}', ["a"], 5)
    many = json.dumps({"steps": [{"agent": "a", "task": str(i)} for i in range(10)]})
    assert len(parse_plan(many, ["a"], 3)) == 3
