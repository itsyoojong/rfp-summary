"""Small SQLite repository. Atomic domain writes and transactional outbox.

JSON preserves validated model snapshots; indexed columns enforce workflow uniqueness.
No network I/O is performed while a SQLite transaction is open.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

from .models import AgentMessage, DailyReport, Meeting, Model, Project, Task, new_id, now_iso

T = TypeVar("T", bound=Model)
MODEL_TYPES = {"project": Project, "task": Task, "meeting": Meeting, "report": DailyReport}


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES (1);
            CREATE TABLE IF NOT EXISTS entities (
                kind TEXT NOT NULL, id TEXT NOT NULL, project_id TEXT NOT NULL,
                status TEXT, day TEXT, revision INTEGER NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (kind,id)
            );
            CREATE INDEX IF NOT EXISTS entities_project ON entities(kind,project_id);
            CREATE UNIQUE INDEX IF NOT EXISTS report_per_day ON entities(project_id,day)
                WHERE kind='report';
            CREATE UNIQUE INDEX IF NOT EXISTS one_running_meeting ON entities(project_id)
                WHERE kind='meeting' AND status='Running';
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                role TEXT NOT NULL, payload TEXT NOT NULL,
                UNIQUE(meeting_id,sequence)
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, meeting_id TEXT NOT NULL,
                actor_id INTEGER NOT NULL, decision TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS approved_once ON approvals(meeting_id)
                WHERE decision='Approved';
            CREATE TABLE IF NOT EXISTS activity (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, action TEXT NOT NULL,
                actor_id INTEGER, details TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                kind TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                lease_until TEXT, owner TEXT, attempt_started INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(kind,entity_id)
            );
            CREATE TABLE IF NOT EXISTS sync_state (
                kind TEXT NOT NULL, entity_id TEXT NOT NULL, metadata TEXT NOT NULL,
                PRIMARY KEY(kind,entity_id)
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                report_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                updated_at TEXT NOT NULL, error TEXT
            );
        """)

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def close(self):
        self.connection.close()

    def put(self, kind: str, entity: Model, *, enqueue: bool = True):
        data = entity.model_dump(mode="json")
        project_id = data["id"] if kind == "project" else data["project_id"]
        payload = json.dumps(data, ensure_ascii=False)
        self.connection.execute("""
            INSERT INTO entities(kind,id,project_id,status,day,revision,payload,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(kind,id) DO UPDATE SET status=excluded.status,day=excluded.day,
                revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at
        """, (kind, data["id"], project_id, data.get("status"), data.get("report_date"),
              data["revision"], payload, now_iso()))
        if enqueue:
            # Keep attempt_started: an unresolved create remains ambiguous across newer versions.
            self.connection.execute("""
                INSERT INTO outbox(kind,entity_id,revision,payload) VALUES(?,?,?,?)
                ON CONFLICT(kind,entity_id) DO UPDATE SET revision=excluded.revision,
                    payload=excluded.payload,
                    status=CASE WHEN outbox.status='conflict' THEN 'conflict' ELSE 'pending' END
            """, (kind, data["id"], data["revision"], payload))

    def get(self, kind: str, entity_id: str):
        row = self.connection.execute("SELECT payload FROM entities WHERE kind=? AND id=?",
                                      (kind, entity_id)).fetchone()
        if row is None:
            raise ValueError(f"{kind} 항목을 찾을 수 없습니다: {entity_id}")
        return MODEL_TYPES[kind].model_validate_json(row["payload"])

    def list(self, kind: str, project_id: str | None = None):
        if project_id is None:
            rows = self.connection.execute("SELECT payload FROM entities WHERE kind=? ORDER BY rowid",
                                           (kind,)).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT payload FROM entities WHERE kind=? AND project_id=? ORDER BY rowid",
                (kind, project_id)).fetchall()
        return [MODEL_TYPES[kind].model_validate_json(row["payload"]) for row in rows]

    def report_on(self, project_id: str, day: str) -> DailyReport | None:
        row = self.connection.execute(
            "SELECT payload FROM entities WHERE kind='report' AND project_id=? AND day=?",
            (project_id, day)).fetchone()
        return DailyReport.model_validate_json(row["payload"]) if row else None

    def add_message(self, message: AgentMessage):
        self.connection.execute("INSERT INTO messages VALUES(?,?,?,?,?)",
                                (message.id, message.meeting_id, message.sequence,
                                 message.role.value, message.model_dump_json()))

    def messages(self, meeting_id: str) -> list[AgentMessage]:
        rows = self.connection.execute("SELECT payload FROM messages WHERE meeting_id=? ORDER BY sequence",
                                       (meeting_id,)).fetchall()
        return [AgentMessage.model_validate_json(row["payload"]) for row in rows]

    def activity(self, project_id: str, action: str, actor_id: int | None, details: dict):
        self.connection.execute("INSERT INTO activity VALUES(?,?,?,?,?,?)",
                                (new_id(), project_id, action, actor_id,
                                 json.dumps(details, ensure_ascii=False), now_iso()))

    def approval(self, project_id: str, meeting_id: str, actor_id: int, approve: bool):
        self.connection.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?)",
                                (new_id(), project_id, meeting_id, actor_id,
                                 "Approved" if approve else "Held", now_iso()))

    def sync_summary(self) -> dict:
        return {row["status"]: row["count"] for row in self.connection.execute(
            "SELECT status,COUNT(*) AS count FROM outbox GROUP BY status").fetchall()}

    def claim_sync(self) -> dict | None:
        timestamp = now_iso()
        with self.transaction():
            row = self.connection.execute("""
                SELECT * FROM outbox WHERE status='pending'
                OR (status='syncing' AND lease_until<?) ORDER BY rowid LIMIT 1
            """, (timestamp,)).fetchone()
            if not row:
                return None
            result = dict(row)
            owner = new_id()
            prior = self.connection.execute("SELECT metadata FROM sync_state WHERE kind=? AND entity_id=?",
                                            (row["kind"], row["entity_id"])).fetchone()
            result["prior"] = json.loads(prior["metadata"]) if prior else {}
            if row["attempt_started"] and not result["prior"].get("page_id"):
                result["prior"]["ambiguous_create"] = True
            result["owner"] = owner
            self.connection.execute("""
                UPDATE outbox SET status='syncing',attempts=attempts+1,attempt_started=1,
                    owner=?,lease_until=? WHERE kind=? AND entity_id=?
            """, (owner, (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                  row["kind"], row["entity_id"]))
            return result

    def sync_success(self, item: dict, metadata: dict):
        with self.transaction():
            self.connection.execute("""
                INSERT INTO sync_state VALUES(?,?,?) ON CONFLICT(kind,entity_id)
                    DO UPDATE SET metadata=excluded.metadata
            """, (item["kind"], item["entity_id"], json.dumps(metadata)))
            self.connection.execute("""
                DELETE FROM outbox WHERE kind=? AND entity_id=? AND revision=? AND owner=?
            """, (item["kind"], item["entity_id"], item["revision"], item["owner"]))

    def sync_failure(self, item: dict, *, conflict: bool, error: str, definitely_not_created: bool = False):
        self.connection.execute("""
            UPDATE outbox SET status=?,last_error=?,lease_until=NULL,
                attempt_started=CASE WHEN ? THEN 0 ELSE attempt_started END
            WHERE kind=? AND entity_id=? AND owner=?
        """, ("conflict" if conflict else "failed", error[:300],
              definitely_not_created, item["kind"], item["entity_id"], item["owner"]))

    def retry_sync(self, *, conflicts: bool = False):
        # Explicit operator retry never clears ambiguous-create protection or remote baseline.
        self.connection.execute("UPDATE outbox SET status='pending' WHERE status='failed'")
        if conflicts:
            self.connection.execute("UPDATE outbox SET status='pending' WHERE status='conflict'")

    def claim_delivery(self, report_id: str) -> bool:
        with self.transaction():
            row = self.connection.execute("SELECT status FROM deliveries WHERE report_id=?",
                                          (report_id,)).fetchone()
            if row and row["status"] in {"sending", "sent", "uncertain"}:
                return False
            self.connection.execute("""
                INSERT INTO deliveries VALUES(?,'sending',?,NULL)
                ON CONFLICT(report_id) DO UPDATE SET status='sending',updated_at=excluded.updated_at,error=NULL
            """, (report_id, now_iso()))
            return True

    def finish_delivery(self, report_id: str, *, error: str | None = None):
        self.connection.execute("UPDATE deliveries SET status=?,updated_at=?,error=? WHERE report_id=?",
                                ("uncertain" if error else "sent", now_iso(), error, report_id))

    def delivery_status(self, report_id: str) -> str | None:
        row = self.connection.execute("SELECT status FROM deliveries WHERE report_id=?", (report_id,)).fetchone()
        return row["status"] if row else None
