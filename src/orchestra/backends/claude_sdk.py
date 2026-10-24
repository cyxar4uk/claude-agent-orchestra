"""Backend that runs each step as one Claude Agent SDK ``query()`` session."""

from __future__ import annotations

from typing import Any

from orchestra.backends.base import BackendError, StepRequest, StepResult
from orchestra.hooks import make_audit_hooks


def build_options(request: StepRequest) -> Any:
    """Translate a StepRequest into ``ClaudeAgentOptions`` (separate so tests can inspect it)."""
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

    pre, post = make_audit_hooks(request.emit, request.step_key, request.agent.name)
    tools = list(request.agent.tools)
    return ClaudeAgentOptions(
        model=request.model,
        system_prompt=request.system_prompt,
        cwd=str(request.cwd),
        tools=tools,  # the ONLY built-in tools this agent can see
        allowed_tools=tools,  # ...and they are pre-approved (no interactive prompt in headless mode)
        permission_mode=request.permission_mode,  # type: ignore[arg-type]
        max_turns=request.max_turns,
        max_budget_usd=request.max_budget_usd,
        setting_sources=["project"] if request.load_project_settings else [],
        hooks={
            "PreToolUse": [HookMatcher(hooks=[pre])],
            "PostToolUse": [HookMatcher(hooks=[post])],
        },
    )


class ClaudeSDKBackend:
    name = "claude"

    async def run_step(self, request: StepRequest) -> StepResult:
        try:
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeSDKError,
                ResultMessage,
                TextBlock,
                query,
            )
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise BackendError("claude-agent-sdk is not installed") from exc

        options = build_options(request)
        texts: list[str] = []
        result: ResultMessage | None = None
        try:
            async for message in query(prompt=request.prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            texts.append(block.text)
                elif isinstance(message, ResultMessage):
                    result = message
        except ClaudeSDKError as exc:
            raise BackendError(f"Claude Agent SDK error: {exc}") from exc

        if result is None:
            raise BackendError("Claude Agent SDK returned no result message")
        if result.is_error:
            detail = "; ".join(result.errors or []) or result.result or result.subtype
            raise BackendError(f"Claude run failed: {detail}")

        output = (result.result or "\n\n".join(texts)).strip()
        return StepResult(
            output=output,
            cost_usd=result.total_cost_usd,
            session_id=result.session_id,
            usage=result.usage or {},
        )
