from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
  target TEXT NOT NULL, state TEXT NOT NULL, snapshot_id TEXT,
  plan_id TEXT, external_id TEXT, error TEXT, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id TEXT PRIMARY KEY, target TEXT NOT NULL, schema_version INTEGER NOT NULL,
  fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, target TEXT NOT NULL,
  snapshot_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
  total_quantity INTEGER NOT NULL, box_count INTEGER NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS write_audit (
  attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, operation TEXT NOT NULL,
  request_fingerprint TEXT NOT NULL, result TEXT, external_id TEXT,
  detail TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS leases (
  lease_key TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL
);
"""


class WorkflowStore:
    """SQLite store with one connection per operation and explicit transactions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_job(self, job_id: str, idempotency_key: str, target: str) -> tuple[dict, bool]:
        with self.connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,NULL,NULL,NULL,NULL,?)",
                    (job_id, idempotency_key, target, "NEW", time.time()),
                )
                created = True
            except sqlite3.IntegrityError:
                created = False
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            return dict(row), created

    def job(self, job_id: str) -> dict:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return dict(row)

    def transition(self, job_id: str, expected: str, new: str, **fields) -> None:
        allowed = {"snapshot_id", "plan_id", "external_id", "error"}
        if set(fields) - allowed:
            raise ValueError("unsupported transition field")
        assignments = ["state=?", "updated_at=?"] + [f"{name}=?" for name in fields]
        values = [new, time.time(), *fields.values(), job_id, expected]
        with self.connection() as connection:
            changed = connection.execute(
                f"UPDATE jobs SET {','.join(assignments)} WHERE job_id=? AND state=?", values
            )
            if changed.rowcount != 1:
                raise RuntimeError(f"state conflict: expected {expected}")

    def save_snapshot(self, snapshot_id: str, target: str, schema_version: int,
                      fingerprint: str, payload: dict, created_at: float) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?,?)",
                (snapshot_id, target, schema_version, fingerprint,
                 json.dumps(payload, sort_keys=True), created_at),
            )

    def save_plan(self, *, plan_id: str, job_id: str, target: str, snapshot_id: str,
                  fingerprint: str, total_quantity: int, box_count: int) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO plans VALUES (?,?,?,?,?,?,?,0,?)",
                (plan_id, job_id, target, snapshot_id, fingerprint,
                 total_quantity, box_count, time.time()),
            )

    def confirm_plan(self, plan_id: str) -> None:
        with self.connection() as connection:
            changed = connection.execute(
                "UPDATE plans SET confirmed=1 WHERE plan_id=? AND confirmed=0", (plan_id,)
            )
            if changed.rowcount != 1:
                raise RuntimeError("plan missing or already confirmed")

    def plan(self, plan_id: str) -> dict:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
            if row is None:
                raise KeyError(plan_id)
            return dict(row)

    def begin_write(self, attempt_id: str, job_id: str, operation: str,
                    request_fingerprint: str) -> tuple[dict, bool]:
        now = time.time()
        with self.connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO write_audit VALUES (?,?,?,?,NULL,NULL,NULL,?,?)",
                    (attempt_id, job_id, operation, request_fingerprint, now, now),
                )
                created = True
            except sqlite3.IntegrityError:
                created = False
            row = connection.execute(
                "SELECT * FROM write_audit WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            return dict(row), created

    def finish_write(self, attempt_id: str, result: str, external_id: str | None,
                     detail: str = "") -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE write_audit SET result=?,external_id=?,detail=?,updated_at=? "
                "WHERE attempt_id=?", (result, external_id, detail, time.time(), attempt_id),
            )

    def write_attempt(self, attempt_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM write_audit WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            return dict(row) if row else None

    def acquire_lease(self, key: str, owner: str, ttl: float, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self.connection() as connection:
            connection.execute("DELETE FROM leases WHERE lease_key=? AND expires_at<=?", (key, now))
            try:
                connection.execute(
                    "INSERT INTO leases VALUES (?,?,?)", (key, owner, now + ttl)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def release_lease(self, key: str, owner: str) -> bool:
        with self.connection() as connection:
            changed = connection.execute(
                "DELETE FROM leases WHERE lease_key=? AND owner=?", (key, owner)
            )
            return changed.rowcount == 1
