"""Audit hooks: turn Claude Agent SDK tool-use hooks into orchestrator events."""

from __future__ import annotations

from typing import Any

from orchestra.backends.base import Emit

_MAX = 500


def _clip(value: Any) -> Any:
    text = str(value)
    return text if len(text) <= _MAX else text[:_MAX] + "...[truncated]"


def make_audit_hooks(emit: Emit, step_key: str, agent: str) -> tuple[Any, Any]:
    """Return ``(pre_tool_use, post_tool_use)`` async hook callbacks bound to one step."""

    async def pre_tool_use(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict:
        emit(
            "tool_use",
            {
                "step": step_key,
                "agent": agent,
                "tool": input_data.get("tool_name", "unknown"),
                "input": _clip(input_data.get("tool_input", {})),
                "tool_use_id": tool_use_id,
            },
        )
        return {}

    async def post_tool_use(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict:
        emit(
            "tool_result",
            {
                "step": step_key,
                "agent": agent,
                "tool": input_data.get("tool_name", "unknown"),
                "tool_use_id": tool_use_id,
            },
        )
        return {}

    return pre_tool_use, post_tool_use
