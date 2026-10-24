import time

import pytest
from fastapi.testclient import TestClient

from orchestra.api import create_app


@pytest.fixture()
def client(engine):
    with TestClient(create_app(engine)) as c:
        yield c


def test_health_and_listings(client):
    assert client.get("/health").json()["status"] == "ok"
    agents = {a["name"]: a for a in client.get("/agents").json()}
    assert agents["planner"]["model"] == "claude-sonnet-5-5"
    wfs = {w["name"]: w for w in client.get("/workflows").json()}
    assert wfs["feature-planning"]["default"] is True
    assert client.get("/knowledge").json() == []


def test_create_run_wait_returns_completed_details(client):
    r = client.post("/runs?wait=true", json={"goal": "Build a thing"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "completed" and len(body["steps"]) == 3

    assert client.get(f"/runs/{body['id']}").json()["workflow"] == "feature-planning"
    arts = client.get(f"/runs/{body['id']}/artifacts").json()
    assert any(a["kind"] == "result" for a in arts)
    events = client.get(f"/runs/{body['id']}/events").json()
    assert events[0]["type"] == "run_created" and events[-1]["type"] == "run_completed"
    assert [r["id"] for r in client.get("/runs").json()] == [body["id"]]


def test_background_run_completes(client):
    run_id = client.post("/runs", json={"goal": "Background goal"}).json()["id"]
    for _ in range(100):
        if client.get(f"/runs/{run_id}").json()["status"] == "completed":
            return
        time.sleep(0.05)
    pytest.fail("background run did not complete")


def test_approval_flow_over_http(client):
    run = client.post(
        "/runs?wait=true", json={"goal": "Edit stuff", "workflow": "implement"}
    ).json()
    assert run["status"] == "awaiting_approval"
    assert client.post(f"/runs/{run['id']}/approve?wait=true").json()["status"] == "completed"
    # a second approval is a conflict
    assert client.post(f"/runs/{run['id']}/approve").status_code == 409


def test_reject_over_http(client):
    run = client.post(
        "/runs?wait=true", json={"goal": "Edit stuff", "workflow": "implement"}
    ).json()
    r = client.post(f"/runs/{run['id']}/reject", json={"reason": "nope"})
    assert r.json()["status"] == "rejected"
    assert client.post(f"/runs/{run['id']}/reject").status_code == 409


def test_errors(client):
    assert client.get("/runs/unknown").status_code == 404
    assert client.post("/runs", json={"goal": "x"}).status_code == 422  # too short
    assert client.post("/runs", json={"goal": "valid goal", "workflow": "nope"}).status_code == 404


def test_bearer_token_protects_everything_but_health(engine, settings):
    engine.settings = settings.model_copy(update={"api_token": "s3cret"})
    with TestClient(create_app(engine, engine.settings)) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/agents").status_code == 401
        assert c.get("/agents", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/agents", headers={"Authorization": "Bearer s3cret"}).status_code == 200
