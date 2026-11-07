import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.schemas import Job

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(settings.data_dir / "jobs.db", check_same_thread=False)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)"
        )
        _conn.commit()
    return _conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def video_path(job_id: str):
    return settings.jobs_dir / job_id / "final.mp4"


def save(job: Job) -> None:
    with _lock:
        db = _db()
        db.execute(
            "INSERT INTO jobs (id, created_at, data) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (job.id, job.created_at, job.model_dump_json()),
        )
        db.commit()


def get(job_id: str) -> Optional[Job]:
    with _lock:
        row = _db().execute("SELECT data FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    job = Job.model_validate_json(row[0])
    job.video_ready = video_path(job_id).exists()
    return job


def list_all() -> list[Job]:
    with _lock:
        rows = _db().execute("SELECT data FROM jobs ORDER BY created_at DESC").fetchall()
    jobs = [Job.model_validate_json(r[0]) for r in rows]
    for job in jobs:
        job.video_ready = video_path(job.id).exists()
    return jobs


def fail_interrupted() -> None:
    for job in list_all():
        if job.status in ("scripting", "rendering"):
            job.status = "failed"
            job.error = "Interrupted by server restart"
            save(job)
