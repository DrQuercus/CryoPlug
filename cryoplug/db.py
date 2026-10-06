"""SQLite persistence for projects and jobs.

Only the server process writes to the database.  Workers communicate through
files in their job directory (see :mod:`cryoplug.worker`), which keeps the
design safe on shared/NFS filesystems used by cluster lanes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    uid TEXT PRIMARY KEY,
    num INTEGER,
    title TEXT,
    description TEXT,
    dir TEXT,
    created_at REAL,
    updated_at REAL,
    archived INTEGER DEFAULT 0,
    job_counter INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
    project_uid TEXT NOT NULL,
    uid TEXT NOT NULL,
    num INTEGER,
    type TEXT,
    title TEXT,
    status TEXT,
    params TEXT,
    inputs TEXT,
    outputs TEXT,
    lane TEXT,
    resources TEXT,
    highlights TEXT,
    notes TEXT DEFAULT '',
    progress REAL DEFAULT 0,
    message TEXT DEFAULT '',
    error TEXT DEFAULT '',
    pid INTEGER,
    cluster_job_id TEXT,
    gpus TEXT,
    created_at REAL,
    queued_at REAL,
    started_at REAL,
    ended_at REAL,
    PRIMARY KEY (project_uid, uid)
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
"""

JSON_FIELDS = {"params", "inputs", "outputs", "resources", "highlights", "gpus"}
JSON_DEFAULTS: dict[str, Any] = {
    "params": {},
    "inputs": {},
    "outputs": [],
    "resources": {},
    "highlights": [],
    "gpus": [],
}
JOB_COLUMNS = (
    "project_uid", "uid", "num", "type", "title", "status", "params", "inputs", "outputs", "lane",
    "resources", "highlights", "notes", "progress", "message", "error", "pid", "cluster_job_id",
    "gpus", "created_at", "queued_at", "started_at", "ended_at",
)
PROJECT_COLUMNS = ("uid", "num", "title", "description", "dir", "created_at", "updated_at", "archived", "job_counter")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ helpers
    def _execute(self, sql: str, args: Iterable[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(args))

    # Execute and fetch under the same lock: the connection is shared between the API threads and the
    # scheduler thread, and a cursor read after the lock is released can be reset by another thread.
    def _one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(args)).fetchone()

    def _all(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(args)).fetchall()

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        for key in JSON_FIELDS:
            raw = job.get(key)
            if raw:
                job[key] = json.loads(raw)
            else:
                job[key] = json.loads(json.dumps(JSON_DEFAULTS[key]))
        return job

    # ----------------------------------------------------------------- projects
    def create_project(self, title: str, description: str, directory: str) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            row = self._one("SELECT COALESCE(MAX(num), 0) + 1 FROM projects")
            num = int(row[0])
            uid = f"P{num}"
            self._execute(
                "INSERT INTO projects (uid, num, title, description, dir, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (uid, num, title, description, directory, now, now),
            )
        return self.get_project(uid)  # type: ignore[return-value]

    def get_project(self, uid: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM projects WHERE uid = ?", (uid,))
        return dict(row) if row else None

    def list_projects(self, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY num DESC"
        projects = [dict(r) for r in self._all(sql)]
        counts = {
            r["project_uid"]: (r["n"], r["active"])
            for r in self._all(
                "SELECT project_uid, COUNT(*) AS n, "
                "SUM(CASE WHEN status IN ('queued','launched','running','waiting') THEN 1 ELSE 0 END) AS active "
                "FROM jobs GROUP BY project_uid"
            )
        }
        for p in projects:
            p["num_jobs"], p["num_active"] = counts.get(p["uid"], (0, 0))
        return projects

    def update_project(self, uid: str, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in PROJECT_COLUMNS and k != "uid"}
        if not allowed:
            return
        allowed["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in allowed)
        self._execute(f"UPDATE projects SET {cols} WHERE uid = ?", (*allowed.values(), uid))

    def delete_project(self, uid: str) -> None:
        with self._lock:
            self._execute("DELETE FROM jobs WHERE project_uid = ?", (uid,))
            self._execute("DELETE FROM projects WHERE uid = ?", (uid,))

    # --------------------------------------------------------------------- jobs
    def create_job(self, project_uid: str, job_type: str, title: str, params: dict, inputs: dict,
                   lane: str | None, resources: dict) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._execute("BEGIN IMMEDIATE")
            try:
                row = self._one("SELECT job_counter FROM projects WHERE uid = ?", (project_uid,))
                if row is None:
                    raise KeyError(f"Unknown project {project_uid}")
                num = int(row[0]) + 1
                self._execute("UPDATE projects SET job_counter = ?, updated_at = ? WHERE uid = ?", (num, now, project_uid))
                uid = f"J{num}"
                self._execute(
                    "INSERT INTO jobs (project_uid, uid, num, type, title, status, params, inputs, outputs, lane, "
                    "resources, highlights, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project_uid, uid, num, job_type, title, "building", json.dumps(params), json.dumps(inputs),
                     "[]", lane, json.dumps(resources), "[]", now),
                )
                self._execute("COMMIT")
            except Exception:
                self._execute("ROLLBACK")
                raise
        return self.get_job(project_uid, uid)  # type: ignore[return-value]

    def get_job(self, project_uid: str, uid: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM jobs WHERE project_uid = ? AND uid = ?", (project_uid, uid))
        return self._job_from_row(row) if row else None

    def list_jobs(self, project_uid: str | None = None, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        clauses, args = [], []
        if project_uid is not None:
            clauses.append("project_uid = ?")
            args.append(project_uid)
        if statuses is not None:
            statuses = list(statuses)
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            args.extend(statuses)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY project_uid, num"
        return [self._job_from_row(r) for r in self._all(sql, args)]

    def update_job(self, project_uid: str, uid: str, **fields: Any) -> None:
        allowed = {}
        for key, value in fields.items():
            if key not in JOB_COLUMNS or key in ("project_uid", "uid", "num"):
                raise KeyError(f"Cannot update job field '{key}'")
            allowed[key] = json.dumps(value) if key in JSON_FIELDS else value
        if not allowed:
            return
        cols = ", ".join(f"{k} = ?" for k in allowed)
        with self._lock:
            self._execute(f"UPDATE jobs SET {cols} WHERE project_uid = ? AND uid = ?", (*allowed.values(), project_uid, uid))
            self._execute("UPDATE projects SET updated_at = ? WHERE uid = ?", (time.time(), project_uid))

    def delete_job(self, project_uid: str, uid: str) -> None:
        self._execute("DELETE FROM jobs WHERE project_uid = ? AND uid = ?", (project_uid, uid))

    # --------------------------------------------------------------------- meta
    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM meta WHERE key = ?", (key,))
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        self._execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))
