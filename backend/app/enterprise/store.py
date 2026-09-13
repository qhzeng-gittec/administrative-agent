"""SQLite task/event storage and durable, single-worker background jobs."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.support.evolution import SkillRegistry, now


class EnterpriseStore(SkillRegistry):
    def __init__(self, path):
        super().__init__(path)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS enterprise_records (kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,id))")

    def put(self, kind: str, row: dict) -> dict:
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO enterprise_records VALUES (?,?,?)", (kind, row["id"], json.dumps(row, ensure_ascii=False)))
        return row

    def get(self, kind: str, record_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT body FROM enterprise_records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        return json.loads(row[0]) if row else None

    def records(self, kind: str, limit: int = 500) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT body FROM enterprise_records WHERE kind=? ORDER BY rowid DESC LIMIT ?", (kind, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def active_capabilities(self, domain: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT body FROM enterprise_records WHERE kind='capability' "
                              "AND json_extract(body, '$.domain')=? AND json_extract(body, '$.status')='active' "
                              "ORDER BY id", (domain,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def enqueue(self, job_type: str, payload: dict, *, job_id: str | None = None) -> dict:
        row = {"id": job_id or uuid.uuid4().hex, "type": job_type, "payload": payload, "status": "queued", "created_at": now()}
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO enterprise_records VALUES ('job',?,?)", (row["id"], json.dumps(row, ensure_ascii=False)))
        return self.get("job", row["id"])

    def claim(self, job_type: str | None = None) -> dict | None:
        # A lease allows restart recovery and prevents a second local worker from
        # processing the same job. Long mining jobs renew it in the worker loop.
        with self.connection() as db:
            jobs = [(key, json.loads(body)) for key, body in db.execute("SELECT id,body FROM enterprise_records WHERE kind='job' ORDER BY rowid")]
            for key, row in jobs:
                if job_type is not None and row["type"] != job_type:
                    continue
                if row["status"] == "running" and row["lease_until"] < now():
                    row.update(status="failed", error="工作进程中断；任务保留，可明确重试", finished_at=now())
                    db.execute("UPDATE enterprise_records SET body=? WHERE kind='job' AND id=?", (json.dumps(row, ensure_ascii=False), key))
                if row["status"] == "queued":
                    row.update(status="running", lease_until=(datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat())
                    db.execute("UPDATE enterprise_records SET body=? WHERE kind='job' AND id=?", (json.dumps(row, ensure_ascii=False), key))
                    return row
        return None

    def renew(self, job: dict) -> None:
        job["lease_until"] = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
        self.put("job", job)
