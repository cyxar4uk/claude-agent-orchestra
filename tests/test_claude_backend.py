"""The Claude SDK backend is tested with a patched ``query``: no API key, no network."""

from __future__ import annotations

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from orchestra.backends.base import BackendError, StepRequest
from orchestra.backends.claude_sdk import ClaudeSDKBackend, build_options
from orchestra.spec import AgentSpec


def make_request(tmp_path, **kw) -> StepRequest:
    agent = AgentSpec(name="dev", description="d", tools=["Read", "Grep"])
    base = {
        "run_id": "r1",
        "step_key": "s1",
        "kind": "task",
        "agent": agent,
        "model": "claude-sonnet-5-5",
        "system_prompt": "SYS",
        "prompt": "do it",
        "cwd": tmp_path,
        "permission_mode": "acceptEdits",
        "max_turns": 7,
    }
    base.update(kw)
    return StepRequest(**base)


def result_message(**kw) -> ResultMessage:
    base = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "sess-1",
        "total_cost_usd": 0.0123,
        "result": "final answer",
    }
    base.update(kw)
    return ResultMessage(**base)


def test_build_options_maps_request(tmp_path):
    opts = build_options(make_request(tmp_path))
    assert opts.model == "claude-sonnet-5-5"
    assert opts.system_prompt == "SYS"
    assert opts.tools == ["Read", "Grep"] and opts.allowed_tools == ["Read", "Grep"]
    assert opts.permission_mode == "acceptEdits" and opts.max_turns == 7
    assert opts.setting_sources == ["project"]
    assert set(opts.hooks) == {"PreToolUse", "PostToolUse"}


def test_project_settings_can_be_disabled(tmp_path):
    opts = build_options(make_request(tmp_path, load_project_settings=False))
    assert opts.setting_sources == []


async def test_run_step_collects_result(monkeypatch, tmp_path):
    captured = {}

    async def fake_query(*, prompt, options):
        captured["prompt"], captured["options"] = prompt, options
        yield AssistantMessage(content=[TextBlock(text="thinking")], model="m")
        yield result_message()

    monkeypatch.setattr("claude_agent_sdk.query", fake_query)
    res = await ClaudeSDKBackend().run_step(make_request(tmp_path))

    assert res.output == "final answer" and res.cost_usd == 0.0123 and res.session_id == "sess-1"
    assert captured["prompt"] == "do it" and captured["options"].model == "claude-sonnet-5-5"


async def test_error_result_raises_backend_error(monkeypatch, tmp_path):
    async def fake_query(*, prompt, options):
        yield result_message(
            is_error=True, subtype="error_max_turns", result=None, errors=["too many turns"]
        )

    monkeypatch.setattr("claude_agent_sdk.query", fake_query)
    with pytest.raises(BackendError, match="too many turns"):
        await ClaudeSDKBackend().run_step(make_request(tmp_path))


async def test_missing_result_raises(monkeypatch, tmp_path):
    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[TextBlock(text="hi")], model="m")

    monkeypatch.setattr("claude_agent_sdk.query", fake_query)
    with pytest.raises(BackendError, match="no result"):
        await ClaudeSDKBackend().run_step(make_request(tmp_path))


async def test_audit_hooks_emit_events(tmp_path):
    events = []
    opts = build_options(make_request(tmp_path, emit=lambda t, d: events.append((t, d))))
    pre = opts.hooks["PreToolUse"][0].hooks[0]
    post = opts.hooks["PostToolUse"][0].hooks[0]
    await pre({"tool_name": "Read", "tool_input": {"file_path": "x" * 1000}}, "tu1", None)
    await post({"tool_name": "Read"}, "tu1", None)
    assert [e[0] for e in events] == ["tool_use", "tool_result"]
    assert events[0][1]["tool"] == "Read" and len(events[0][1]["input"]) < 600
