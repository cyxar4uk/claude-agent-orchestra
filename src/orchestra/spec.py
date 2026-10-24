"""Declarative configuration: agents, workflows, knowledge. Loaded from ``orchestra.yaml``."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_SHARED_RULES = """\
Team rules:
- Inspect the available files before claiming anything about them; never invent file names.
- Prefer small, reviewable steps over sweeping changes.
- Stay inside your role; do not do other agents' jobs.
- End every answer with four short sections: What was done / What remains / Risks / Next step.
"""


class AgentSpec(BaseModel):
    name: str = ""
    description: str
    prompt: str = ""
    prompt_file: str | None = None
    tools: list[str] = Field(default_factory=list)
    model: str = "worker"  # "worker", "planner" or a literal model ID
    permission_mode: str | None = None
    max_turns: int | None = None
    use_knowledge: bool = True


class StepSpec(BaseModel):
    id: str
    agent: str
    task: str  # may contain {{ goal }}, {{ previous }}, {{ steps.<id> }}


class WorkflowSpec(BaseModel):
    name: str = ""
    description: str = ""
    mode: Literal["pipeline", "planner"] = "pipeline"
    steps: list[StepSpec] = Field(default_factory=list)  # pipeline mode
    planner: str | None = None  # planner mode: agent that writes the plan
    agents: list[str] | None = None  # planner mode: agents the plan may use (default: all others)
    max_steps: int = 6
    require_approval: bool = False

    @model_validator(mode="after")
    def _check_mode(self) -> WorkflowSpec:
        if self.mode == "pipeline":
            if not self.steps:
                raise ValueError("pipeline workflow needs at least one step")
            ids = [s.id for s in self.steps]
            if len(set(ids)) != len(ids):
                raise ValueError("step ids must be unique")
        elif not self.planner:
            raise ValueError("planner workflow needs a 'planner' agent")
        return self


class OrchestraConfig(BaseModel):
    shared_rules: str = DEFAULT_SHARED_RULES
    knowledge_dir: str | None = None
    default_workflow: str | None = None
    agents: dict[str, AgentSpec]
    workflows: dict[str, WorkflowSpec]
    base_dir: Path = Path(".")  # directory of the config file (for relative paths)

    @model_validator(mode="after")
    def _check_refs(self) -> OrchestraConfig:
        for key, agent in self.agents.items():
            agent.name = key
        for key, wf in self.workflows.items():
            wf.name = key
            for step in wf.steps:
                if step.agent not in self.agents:
                    raise ValueError(f"workflow '{key}': unknown agent '{step.agent}'")
            if wf.planner and wf.planner not in self.agents:
                raise ValueError(f"workflow '{key}': unknown planner agent '{wf.planner}'")
            for name in wf.agents or []:
                if name not in self.agents:
                    raise ValueError(f"workflow '{key}': unknown agent '{name}'")
        if self.default_workflow is None and self.workflows:
            self.default_workflow = next(iter(self.workflows))
        if self.default_workflow and self.default_workflow not in self.workflows:
            raise ValueError(f"default_workflow '{self.default_workflow}' is not defined")
        return self

    @property
    def knowledge_path(self) -> Path | None:
        if not self.knowledge_dir:
            return None
        p = Path(self.knowledge_dir).expanduser()
        return p if p.is_absolute() else (self.base_dir / p).resolve()


def _resolve_prompts(raw: dict[str, Any], base_dir: Path | None) -> None:
    """Inline ``prompt_file`` contents. ``base_dir=None`` reads from the packaged defaults."""
    for agent in (raw.get("agents") or {}).values():
        pf = agent.get("prompt_file")
        if not pf or agent.get("prompt"):
            continue
        if base_dir is None:
            text = (resources.files("orchestra") / "defaults" / pf).read_text(encoding="utf-8")
        else:
            p = Path(pf).expanduser()
            p = p if p.is_absolute() else base_dir / p
            text = p.read_text(encoding="utf-8")
        agent["prompt"] = text.strip()


def load_config(path: str | Path | None = None) -> OrchestraConfig:
    """Load a config file, or the built-in default config when ``path`` is empty."""
    if path:
        file = Path(path).expanduser().resolve()
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        base_dir = file.parent
        _resolve_prompts(raw, base_dir)
    else:
        text = (resources.files("orchestra") / "defaults" / "orchestra.yaml").read_text(
            encoding="utf-8"
        )
        raw = yaml.safe_load(text)
        base_dir = Path.cwd()
        _resolve_prompts(raw, None)
    raw["base_dir"] = base_dir
    return OrchestraConfig.model_validate(raw)


_TEMPLATE = re.compile(r"\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}")


def render(template: str, variables: dict[str, str]) -> str:
    """Tiny ``{{ name }}`` substitution. Unknown names render as empty strings."""
    return _TEMPLATE.sub(lambda m: variables.get(m.group(1), ""), template)
