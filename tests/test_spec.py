import pytest
from pydantic import ValidationError

from orchestra.settings import Settings
from orchestra.spec import OrchestraConfig, load_config, render


def test_default_config_loads_prompts_and_workflows(config):
    assert set(config.agents) >= {"planner", "analyst", "architect", "implementer", "reviewer"}
    assert config.agents["analyst"].prompt  # prompt_file was inlined
    assert config.default_workflow == "feature-planning"
    assert config.workflows["implement"].require_approval is True
    assert config.workflows["auto"].mode == "planner"


def test_read_only_default_workflow_has_no_write_tools(config):
    wf = config.workflows["feature-planning"]
    for step in wf.steps:
        tools = set(config.agents[step.agent].tools)
        assert not tools & {"Write", "Edit", "Bash"}


def test_model_defaults_and_aliases():
    s = Settings(_env_file=None)
    assert s.worker_model == "claude-sonnet-5-5"
    assert s.resolve_model("worker") == "claude-sonnet-5-5"
    assert s.resolve_model("planner") == "claude-sonnet-5-5"  # no planner model set
    s2 = Settings(_env_file=None, planner_model="claude-opus-5-5")
    assert s2.resolve_model("planner") == "claude-opus-5-5"
    assert s2.resolve_model("some-custom-model") == "some-custom-model"


def test_env_overrides_models(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_WORKER_MODEL", "my-worker")
    monkeypatch.setenv("ORCHESTRA_PLANNER_MODEL", "my-planner")
    s = Settings(_env_file=None)
    assert (s.worker_model, s.resolved_planner_model) == ("my-worker", "my-planner")


def test_render_substitutes_and_blanks_unknowns():
    out = render(
        "Goal={{ goal }} prev={{previous}} s={{ steps.a-b }} x={{ nope }}",
        {
            "goal": "G",
            "previous": "P",
            "steps.a-b": "S",
        },
    )
    assert out == "Goal=G prev=P s=S x="


def test_unknown_agent_in_workflow_is_rejected():
    with pytest.raises(ValidationError, match="unknown agent"):
        OrchestraConfig.model_validate(
            {
                "agents": {"a": {"description": "d"}},
                "workflows": {"w": {"steps": [{"id": "s", "agent": "ghost", "task": "t"}]}},
            }
        )


def test_duplicate_step_ids_are_rejected():
    with pytest.raises(ValidationError, match="unique"):
        OrchestraConfig.model_validate(
            {
                "agents": {"a": {"description": "d"}},
                "workflows": {
                    "w": {
                        "steps": [
                            {"id": "s", "agent": "a", "task": "t"},
                            {"id": "s", "agent": "a", "task": "t"},
                        ]
                    }
                },
            }
        )


def test_custom_config_resolves_relative_prompt_and_knowledge(tmp_path):
    (tmp_path / "p.md").write_text("You are custom.", encoding="utf-8")
    (tmp_path / "orchestra.yaml").write_text(
        "knowledge_dir: kb\n"
        "agents:\n  a: {description: d, prompt_file: p.md}\n"
        "workflows:\n  w:\n    steps:\n      - {id: s, agent: a, task: do}\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path / "orchestra.yaml")
    assert cfg.agents["a"].prompt == "You are custom."
    assert cfg.knowledge_path == (tmp_path / "kb").resolve()


def test_bundled_examples_are_valid():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "examples"
    for cfg_path in root.glob("*/orchestra.yaml"):
        assert load_config(cfg_path).workflows
