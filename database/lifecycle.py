"""Transactional job lifecycle service used by the GUI.

    pending ──Mark Relevant──▶ selected ──Applied──▶ applied (visible 15 days from ingestion)
       │
       └──Ignore──▶ active record deleted + suppression fingerprint

Every transition runs in one ``BEGIN IMMEDIATE`` transaction and is guarded by
the job's current status, so a job can never be in two lifecycle states and a
double-click cannot apply a transition twice. A suppression fingerprint is
written as soon as a job leaves the pending queue, so neither selected, ignored,
applied nor expired jobs can ever resurface as new opportunities.
"""
from __future__ import annotations

import json

from config.settings import utcnow_iso
from database.connection import Database
from database.repository import applied_expiry


class LifecycleError(RuntimeError):
    pass


class JobLifecycleService:
    def __init__(self, db: Database):
        self.db = db

    # -- helpers --------------------------------------------------------------
    @staticmethod
    def _suppress(conn, job: dict, reason: str) -> None:
        conn.execute(
            "INSERT INTO suppression_ledger(fingerprint, canonical_url_hash, loose_key, has_requisition_id, source_name,"
            " suppressed_at, suppression_reason) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(fingerprint) DO UPDATE SET suppression_reason = excluded.suppression_reason,"
            " suppressed_at = excluded.suppressed_at",
            (job["fingerprint"], job["canonical_url_hash"], job["loose_key"], 1 if job.get("requisition_id") else 0,
             job["source_name"], utcnow_iso(), reason),
        )
        # Remember every portal URL this job was seen under.
        from screening.deduplication import url_hash

        for (src_url,) in conn.execute("SELECT source_url FROM job_sources WHERE job_id = ?", (job["job_id"],)).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO suppressed_urls(canonical_url_hash, fingerprint) VALUES (?,?)",
                (url_hash(src_url), job["fingerprint"]),
            )

    @staticmethod
    def _load(conn, job_id: str, allowed: tuple[str, ...]) -> dict:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise LifecycleError("Job no longer exists (it may have been processed already).")
        if row["status"] not in allowed:
            raise LifecycleError(f"Job is '{row['status']}', expected one of {', '.join(allowed)}.")
        return dict(row)

    # -- transitions ----------------------------------------------------------
    def mark_relevant(self, job_id: str, notes: str | None = None) -> None:
        with self.db.transaction() as conn:
            job = self._load(conn, job_id, ("pending", "duplicate_review"))
            now = utcnow_iso()
            conn.execute("UPDATE jobs SET status = 'selected', updated_at = ? WHERE job_id = ?", (now, job_id))
            conn.execute(
                "INSERT INTO selected_jobs(job_id, selected_at, status, application_notes) VALUES (?,?, 'Selected', ?)",
                (job_id, now, notes),
            )
            self._suppress(conn, job, "selected")

    def ignore(self, job_id: str) -> None:
        """Delete the active record and keep only a minimal suppression fingerprint."""
        with self.db.transaction() as conn:
            job = self._load(conn, job_id, ("pending", "duplicate_review"))
            self._suppress(conn, job, "ignored")
            conn.execute("DELETE FROM job_sources WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))

    def keep_as_distinct(self, job_id: str) -> None:
        """Resolve a duplicate-review item as a genuinely different job."""
        with self.db.transaction() as conn:
            self._load(conn, job_id, ("duplicate_review",))
            conn.execute(
                "UPDATE jobs SET status = 'pending', duplicate_of = NULL, updated_at = ? WHERE job_id = ?",
                (utcnow_iso(), job_id),
            )

    def update_notes(self, job_id: str, notes: str) -> None:
        with self.db.transaction() as conn:
            self._load(conn, job_id, ("selected",))
            conn.execute("UPDATE selected_jobs SET application_notes = ? WHERE job_id = ?", (notes, job_id))

    def mark_applied(self, job_id: str) -> str:
        """Move a selected job to applied. Returns the visibility expiry timestamp.

        Expiry is 15 days from the job's *original ingestion* timestamp, not from
        the time it was marked applied.
        """
        with self.db.transaction() as conn:
            job = self._load(conn, job_id, ("selected",))
            now = utcnow_iso()
            expires_at = applied_expiry(job["ingested_at"])
            conn.execute("UPDATE jobs SET status = 'applied', updated_at = ? WHERE job_id = ?", (now, job_id))
            conn.execute("DELETE FROM selected_jobs WHERE job_id = ?", (job_id,))
            conn.execute(
                "INSERT INTO applied_jobs(job_id, title, company, location, source_name, source_url, matched_skills,"
                " ingested_at, applied_at, application_status, expires_at, is_test_fixture)"
                " VALUES (?,?,?,?,?,?,?,?,?, 'Applied', ?, ?)"
                " ON CONFLICT(job_id) DO UPDATE SET applied_at = excluded.applied_at,"
                " application_status = excluded.application_status, expires_at = excluded.expires_at",
                (job_id, job["title"], job["company"], job["location"], job["source_name"], job["source_url"],
                 job["matched_skills"] or json.dumps([]), job["ingested_at"], now, expires_at, job["is_test_fixture"]),
            )
            self._suppress(conn, job, "applied")
        return expires_at

    def cleanup_expired_applied(self, now_iso: str | None = None) -> int:
        """Remove expired applied jobs' full records; suppression fingerprints remain.

        The applied view already hides expired rows through a query-based rule;
        this cleanup additionally deletes the stored job description.
        """
        now_iso = now_iso or utcnow_iso()
        with self.db.transaction() as conn:
            expired = [r[0] for r in conn.execute("SELECT job_id FROM applied_jobs WHERE expires_at <= ?", (now_iso,))]
            for job_id in expired:
                row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
                if row:
                    self._suppress(conn, dict(row), "applied_expired")
                    conn.execute("DELETE FROM job_sources WHERE job_id = ?", (job_id,))
                    conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
                conn.execute("DELETE FROM applied_jobs WHERE job_id = ?", (job_id,))
        return len(expired)

    def purge_all_job_data(self, keep_suppression: bool = True) -> None:
        """Data-retention control: delete all discovered jobs and logs."""
        with self.db.transaction() as conn:
            for table in ("job_sources", "selected_jobs", "applied_jobs", "jobs", "search_log", "agent_logs",
                          "http_cache"):
                conn.execute(f"DELETE FROM {table}")
            if not keep_suppression:
                conn.execute("DELETE FROM suppression_ledger")
                conn.execute("DELETE FROM suppressed_urls")
