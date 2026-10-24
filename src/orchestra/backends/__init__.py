from __future__ import annotations

from orchestra.backends.base import Backend, BackendError, StepRequest, StepResult
from orchestra.backends.mock import MockBackend
from orchestra.settings import Settings


def create_backend(settings: Settings) -> Backend:
    if settings.backend == "mock":
        return MockBackend()
    from orchestra.backends.claude_sdk import ClaudeSDKBackend

    return ClaudeSDKBackend()


__all__ = [
    "Backend",
    "BackendError",
    "MockBackend",
    "StepRequest",
    "StepResult",
    "create_backend",
]
