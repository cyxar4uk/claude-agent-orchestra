from orchestra.cli import main
from orchestra.knowledge import KnowledgeBase
from orchestra.store import RunStatus, Store


def test_store_roundtrip_and_restart_recovery(tmp_path):
    db = tmp_path / "x" / "o.db"
    store = Store(db)
    run = store.create_run("goal", "wf", True)
    store.set_run_status(run.id, RunStatus.running)
    store.add_event(run.id, "hello", {"a": 1})
    store.close()

    reopened = Store(db)
    assert reopened.fail_interrupted_runs() == 1
    got = reopened.get_run(run.id)
    assert got.status == RunStatus.failed and got.require_approval is True
    assert reopened.list_events(run.id)[0].data == {"a": 1}
    assert reopened.list_runs(status="failed")[0].id == run.id


def test_knowledge_base_renders_and_truncates(tmp_path):
    (tmp_path / "a.md").write_text("alpha " * 10, encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("beta " * 10, encoding="utf-8")
    (tmp_path / "ignored.bin").write_bytes(b"\x00")

    kb = KnowledgeBase(tmp_path, max_chars=10_000)
    text = kb.render()
    assert "### a.md" in text and "### sub/b.txt" in text and "ignored" not in text
    assert [f["path"] for f in kb.listing()] == ["a.md", "sub/b.txt"]

    small = KnowledgeBase(tmp_path, max_chars=30).render()
    assert small.endswith("[knowledge truncated]")
    assert KnowledgeBase(None).render() == ""


def test_knowledge_is_injected_into_prompts(config, settings, tmp_path):
    import asyncio

    from orchestra.backends import MockBackend
    from orchestra.engine import Engine

    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "facts.md").write_text("The sky is green.", encoding="utf-8")
    config.knowledge_dir = str(tmp_path / "kb")
    backend = MockBackend()
    eng = Engine(config, settings, Store(":memory:"), backend)
    asyncio.run(eng.execute(eng.create_run("goal").id))
    assert all("The sky is green." in c.prompt for c in backend.calls)


def test_cli_run_with_mock(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORCHESTRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ORCHESTRA_WORKSPACE", str(tmp_path))
    from orchestra.settings import get_settings

    get_settings.cache_clear()
    assert main(["run", "--mock", "Plan a feature"]) == 0
    out = capsys.readouterr().out
    assert "status=completed" in out and "analyst" in out
    assert main(["agents"]) == 0
    assert main(["workflows"]) == 0
    assert main(["runs"]) == 0
    get_settings.cache_clear()
