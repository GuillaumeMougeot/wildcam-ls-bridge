"""Persistent job store (SQLite) and a single background worker.

One worker thread processes jobs strictly one at a time: two concurrent jobs could both
pass the free-disk-space check and then fill the disk together.
"""

from __future__ import annotations

import json
import logging
import queue
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class JobStatus:
    QUEUED = "queued"
    CHECKING = "checking"  # dry run: ERDA connection + remote sizes + local disk space
    DOWNLOADING = "downloading"
    IMPORTING = "importing"  # creating the Label Studio project
    READY = "ready"  # project exists; human annotation can start / is ongoing
    DRY_RUN_OK = "dry_run_ok"  # dry-run-only job finished successfully
    FAILED = "failed"
    DELETED = "deleted"  # LS project + videos deleted (DELETE /jobs/{id}); record kept

    TERMINAL = frozenset({READY, DRY_RUN_OK, FAILED, DELETED})
    RUNNING = frozenset({CHECKING, DOWNLOADING, IMPORTING})  # cannot be deleted


@dataclass
class Job:
    id: str
    created_at: str
    updated_at: str
    status: str
    dry_run: bool
    title: str
    description: str = ""
    allow_missing: bool = False
    n_items: int = 0
    project_id: int | None = None
    error: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_public(self, ls_public_url: str) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        live = self.project_id is not None and self.status != JobStatus.DELETED
        d["project_url"] = f"{ls_public_url}/projects/{self.project_id}/data" if live else None
        return d


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_job_id() -> str:
    # Fixed length (23 chars): never a prefix of another id (see paths.JOB_ID_RE).
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    dry_run INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    allow_missing INTEGER NOT NULL DEFAULT 0,
    n_items INTEGER NOT NULL DEFAULT 0,
    project_id INTEGER,
    error TEXT,
    progress TEXT NOT NULL DEFAULT '{}',
    report TEXT NOT NULL DEFAULT '{}',
    warnings TEXT NOT NULL DEFAULT '[]'
);
"""
_JSON_FIELDS = ("progress", "report", "warnings")
_UPDATABLE = {"status", "n_items", "project_id", "error", *_JSON_FIELDS}


class JobStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        d = dict(row)
        for k in _JSON_FIELDS:
            d[k] = json.loads(d[k])
        d["dry_run"] = bool(d["dry_run"])
        d["allow_missing"] = bool(d["allow_missing"])
        return Job(**d)

    def create(
        self,
        *,
        title: str,
        description: str,
        dry_run: bool,
        allow_missing: bool,
        n_items: int,
        warnings: list[str],
        job_id: str | None = None,
    ) -> Job:
        now = utcnow()
        job = Job(
            id=job_id or new_job_id(),
            created_at=now,
            updated_at=now,
            status=JobStatus.QUEUED,
            dry_run=dry_run,
            title=title,
            description=description,
            allow_missing=allow_missing,
            n_items=n_items,
            warnings=warnings,
        )
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id, created_at, updated_at, status, dry_run, title, description,"
                " allow_missing, n_items, warnings) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    job.id,
                    now,
                    now,
                    job.status,
                    int(dry_run),
                    title,
                    description,
                    int(allow_missing),
                    n_items,
                    json.dumps(warnings),
                ),
            )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def find_by_project(self, project_id: int) -> Job | None:
        """The job that created Label Studio project ``project_id`` (None: not a bridge project)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def recent(self, limit: int = 100) -> list[Job]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def update(self, job_id: str, **fields: Any) -> None:
        unknown = set(fields) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update {unknown}")
        values = {k: json.dumps(v) if k in _JSON_FIELDS else v for k, v in fields.items()}
        values["updated_at"] = utcnow()
        assignments = ", ".join(f"{k} = ?" for k in values)  # keys come from _UPDATABLE only
        with self._lock, self._conn() as c:
            c.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",  # noqa: S608
                (*values.values(), job_id),
            )

    def fail_interrupted(self) -> list[str]:
        """Mark jobs left running by a crash/restart as failed (they can be retried)."""
        ids = [
            j.id
            for j in self.recent(limit=10_000)
            if j.status not in JobStatus.TERMINAL and j.status != JobStatus.QUEUED
        ]
        for job_id in ids:
            self.update(
                job_id,
                status=JobStatus.FAILED,
                error="interrupted by a service restart; POST /jobs/{id}/retry to resume",
            )
        return ids

    def queued_ids(self) -> list[str]:
        return [j.id for j in reversed(self.recent(limit=10_000)) if j.status == JobStatus.QUEUED]


class JobRunner:
    """Single daemon thread consuming job ids from a queue."""

    def __init__(self, handler: Callable[[str], None]) -> None:
        self._handler = handler
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, job_id: str) -> None:
        self._queue.put(job_id)

    def stop(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        self._thread.join(timeout)

    def join_idle(self) -> None:
        """Block until every submitted job has been processed (used by tests)."""
        self._queue.join()

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                self._handler(job_id)
            except Exception:  # the handler records failures; never kill the worker
                log.exception("unhandled error while processing job %s", job_id)
            finally:
                self._queue.task_done()
