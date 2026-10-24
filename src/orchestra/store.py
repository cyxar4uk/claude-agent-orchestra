"""SQLite persistence for runs, steps, artifacts and events (stdlib ``sqlite3``, no ORM)."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class RunStatus(str, Enum):
    pending = "pending"
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    running = "running"
    completed = "completed"
    failed = "failed"
    rejected = "rejected"


class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class StepRecord(BaseModel):
    id: int
    run_id: str
    idx: int
    key: str
    kind: str  # "plan" | "task"
    agent: str
    task: str
    status: StepStatus
    output: str | None = None
    error: str | None = None
    model: str | None = None
    cost_usd: float | None = None
    session_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class RunRecord(BaseModel):
    id: str
    goal: str
    workflow: str
    status: RunStatus
    require_approval: bool
    error: str | None = None
    created_at: str
    updated_at: str


class ArtifactRecord(BaseModel):
    id: int
    run_id: str
    step_id: int | None = None
    kind: str
    title: str
    content: str
    created_at: str


class EventRecord(BaseModel):
    id: int
    run_id: str
    ts: str
    type: str
    data: dict[str, Any]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    workflow TEXT NOT NULL,
    status TEXT NOT NULL,
    require_approval INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    agent TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT NOT NULL,
    output TEXT,
    error TEXT,
    model TEXT,
    cost_usd REAL,
    session_id TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, idx);
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id INTEGER,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
"""


class Store:
    """Thread-safe SQLite store. Pass ``":memory:"`` for tests."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- helpers ---------------------------------------------------------------------------
    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # -- runs ------------------------------------------------------------------------------
    def create_run(self, goal: str, workflow: str, require_approval: bool) -> RunRecord:
        run_id = uuid.uuid4().hex[:12]
        now = utcnow()
        self._exec(
            "INSERT INTO runs (id, goal, workflow, status, require_approval, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, goal, workflow, RunStatus.pending.value, int(require_approval), now, now),
        )
        run = self.get_run(run_id)
        assert run is not None
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
        rows = self._all("SELECT * FROM runs WHERE id = ?", (run_id,))
        return self._run(rows[0]) if rows else None

    def list_runs(self, limit: int = 50, status: str | None = None) -> list[RunRecord]:
        if status:
            rows = self._all(
                "SELECT * FROM runs WHERE status = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self._all(
                "SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            )
        return [self._run(r) for r in rows]

    def set_run_status(self, run_id: str, status: RunStatus, error: str | None = None) -> None:
        self._exec(
            "UPDATE runs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status.value, error, utcnow(), run_id),
        )

    def fail_interrupted_runs(self) -> int:
        """On startup: runs that were mid-flight when the process died cannot be resumed."""
        cur = self._exec(
            "UPDATE runs SET status = ?, error = ?, updated_at = ? WHERE status IN (?, ?, ?)",
            (
                RunStatus.failed.value,
                "interrupted by server restart",
                utcnow(),
                RunStatus.pending.value,
                RunStatus.planning.value,
                RunStatus.running.value,
            ),
        )
        return cur.rowcount

    @staticmethod
    def _run(row: sqlite3.Row) -> RunRecord:
        d = dict(row)
        d["require_approval"] = bool(d["require_approval"])
        return RunRecord.model_validate(d)

    # -- steps -----------------------------------------------------------------------------
    def add_step(
        self, run_id: str, idx: int, key: str, kind: str, agent: str, task: str
    ) -> StepRecord:
        cur = self._exec(
            "INSERT INTO steps (run_id, idx, key, kind, agent, task, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, idx, key, kind, agent, task, StepStatus.pending.value),
        )
        step = self.get_step(int(cur.lastrowid or 0))
        assert step is not None
        return step

    def get_step(self, step_id: int) -> StepRecord | None:
        rows = self._all("SELECT * FROM steps WHERE id = ?", (step_id,))
        return StepRecord.model_validate(dict(rows[0])) if rows else None

    def list_steps(self, run_id: str) -> list[StepRecord]:
        rows = self._all("SELECT * FROM steps WHERE run_id = ? ORDER BY idx, id", (run_id,))
        return [StepRecord.model_validate(dict(r)) for r in rows]

    def start_step(self, step_id: int, model: str) -> None:
        self._exec(
            "UPDATE steps SET status = ?, model = ?, started_at = ? WHERE id = ?",
            (StepStatus.running.value, model, utcnow(), step_id),
        )

    def finish_step(
        self,
        step_id: int,
        *,
        output: str | None = None,
        error: str | None = None,
        cost_usd: float | None = None,
        session_id: str | None = None,
    ) -> None:
        status = StepStatus.failed if error else StepStatus.completed
        self._exec(
            "UPDATE steps SET status = ?, output = ?, error = ?, cost_usd = ?, session_id = ?,"
            " finished_at = ? WHERE id = ?",
            (status.value, output, error, cost_usd, session_id, utcnow(), step_id),
        )

    # -- artifacts -------------------------------------------------------------------------
    def add_artifact(
        self, run_id: str, kind: str, title: str, content: str, step_id: int | None = None
    ) -> ArtifactRecord:
        cur = self._exec(
            "INSERT INTO artifacts (run_id, step_id, kind, title, content, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, step_id, kind, title, content, utcnow()),
        )
        rows = self._all("SELECT * FROM artifacts WHERE id = ?", (cur.lastrowid,))
        return ArtifactRecord.model_validate(dict(rows[0]))

    def list_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        rows = self._all("SELECT * FROM artifacts WHERE run_id = ? ORDER BY id", (run_id,))
        return [ArtifactRecord.model_validate(dict(r)) for r in rows]

    # -- events ----------------------------------------------------------------------------
    def add_event(self, run_id: str, type_: str, data: dict[str, Any] | None = None) -> None:
        self._exec(
            "INSERT INTO events (run_id, ts, type, data) VALUES (?, ?, ?, ?)",
            (run_id, utcnow(), type_, json.dumps(data or {}, ensure_ascii=False, default=str)),
        )

    def list_events(self, run_id: str, after_id: int = 0, limit: int = 1000) -> list[EventRecord]:
        rows = self._all(
            "SELECT * FROM events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
            (run_id, after_id, limit),
        )
        return [
            EventRecord(
                id=r["id"],
                run_id=r["run_id"],
                ts=r["ts"],
                type=r["type"],
                data=json.loads(r["data"]),
            )
            for r in rows
        ]
