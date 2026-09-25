"""Repository layer: every SQL statement used by the app lives here or in lifecycle.py."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from config.settings import APPLIED_VISIBILITY_DAYS, parse_iso, utcnow, utcnow_iso
from database.connection import Database
from database.models import CandidateProfile
from screening.deduplication import (
    FUZZY_TITLE_THRESHOLD,
    normalize_company,
    normalize_location,
    title_similarity,
)

# ---------------------------------------------------------------------------
# Candidate profiles
# ---------------------------------------------------------------------------


class ProfileRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, resume_filename: str, profile: CandidateProfile, resume_text: str, activate: bool = True) -> int:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            version = (conn.execute("SELECT COALESCE(MAX(resume_version),0) FROM candidate_profiles").fetchone()[0]) + 1
            if activate:
                conn.execute("UPDATE candidate_profiles SET active = 0 WHERE active = 1")
            cur = conn.execute(
                "INSERT INTO candidate_profiles(resume_filename, resume_version, extracted_profile, resume_text, active,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (resume_filename, version, profile.model_dump_json(), resume_text, 1 if activate else 0, now, now),
            )
            return int(cur.lastrowid)

    def update_profile(self, profile_id: int, profile: CandidateProfile) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE candidate_profiles SET extracted_profile = ?, updated_at = ? WHERE profile_id = ?",
                (profile.model_dump_json(), utcnow_iso(), profile_id),
            )

    def set_active(self, profile_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE candidate_profiles SET active = 0 WHERE active = 1")
            conn.execute(
                "UPDATE candidate_profiles SET active = 1, updated_at = ? WHERE profile_id = ?",
                (utcnow_iso(), profile_id),
            )

    def delete(self, profile_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM candidate_profiles WHERE profile_id = ?", (profile_id,))

    def list(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT profile_id, resume_filename, resume_version, active, created_at, updated_at"
                " FROM candidate_profiles ORDER BY resume_version DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, profile_id: int) -> tuple[CandidateProfile, str, dict[str, Any]] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM candidate_profiles WHERE profile_id = ?", (profile_id,)).fetchone()
        if not row:
            return None
        return CandidateProfile.model_validate_json(row["extracted_profile"]), row["resume_text"], dict(row)

    def get_active(self) -> tuple[CandidateProfile, str, dict[str, Any]] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT profile_id FROM candidate_profiles WHERE active = 1").fetchone()
        return self.get(row[0]) if row else None


# ---------------------------------------------------------------------------
# Persisted settings
# ---------------------------------------------------------------------------

DEFAULT_SEARCH_SETTINGS: dict[str, Any] = {
    "target_locations": ["Bangalore", "Hyderabad", "Pune", "Mumbai", "Delhi NCR", "Chennai", "United Kingdom",
                         "Ireland", "Germany", "France", "Netherlands"],
    "bangalore_only": False,
    "remote_only": False,
    "employment_types": ["full-time"],
    "seniority_levels": ["senior", "manager", "lead"],
    "target_titles": ["Service Delivery Manager", "IT Service Management Lead", "Service Operations Manager",
                      "AI Program Manager", "Site Reliability Manager"],
    "date_posted_days": 30,
    "min_relevance_score": 0,
    "max_results_per_search": 50,
    "use_llm": True,
    "allow_resume_to_llm": None,  # None -> fall back to ALLOW_RESUME_TO_LLM env var
    "relevance_weights": {"skill_coverage": 0.5, "title_alignment": 0.2, "evidence_completeness": 0.2, "recency": 0.1},
}


class SettingsRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO app_settings(key, value, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(value), utcnow_iso()),
            )

    def search_settings(self) -> dict[str, Any]:
        stored = self.get("search_settings", {}) or {}
        merged = {**DEFAULT_SEARCH_SETTINGS, **stored}
        merged["relevance_weights"] = {**DEFAULT_SEARCH_SETTINGS["relevance_weights"], **(stored.get("relevance_weights") or {})}
        return merged

    def save_search_settings(self, values: dict[str, Any]) -> None:
        self.set("search_settings", {**self.search_settings(), **values})


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------


class SourceRegistryRepository:
    def __init__(self, db: Database):
        self.db = db

    def ensure(self, source_name: str, enabled_by_default: bool, config: dict | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO source_registry(source_name, enabled, status, config) VALUES (?,?,?,?)",
                (source_name, 1 if enabled_by_default else 0, "untested", json.dumps(config or {})),
            )

    def list(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM source_registry ORDER BY source_name").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["config"] = json.loads(d.get("config") or "{}")
            d["enabled"] = bool(d["enabled"])
            out.append(d)
        return out

    def get(self, source_name: str) -> dict[str, Any] | None:
        return next((s for s in self.list() if s["source_name"] == source_name), None)

    def set_enabled(self, source_name: str, enabled: bool) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE source_registry SET enabled = ? WHERE source_name = ?", (1 if enabled else 0, source_name))

    def set_config(self, source_name: str, config: dict) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE source_registry SET config = ? WHERE source_name = ?", (json.dumps(config), source_name))

    def set_status(self, source_name: str, status: str, error: str | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE source_registry SET status = ?, last_error = ?, last_checked_at = ? WHERE source_name = ?",
                (status, error, utcnow_iso(), source_name),
            )

    def record_success(self, source_name: str) -> None:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE source_registry SET status = 'working', last_success_at = ?, last_checked_at = ?,"
                " last_error = NULL, failure_count = 0 WHERE source_name = ?",
                (now, now, source_name),
            )

    def record_failure(self, source_name: str, status: str, error: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE source_registry SET status = ?, last_error = ?, last_checked_at = ?,"
                " failure_count = failure_count + 1 WHERE source_name = ?",
                (status, error[:500], utcnow_iso(), source_name),
            )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@dataclass
class DuplicateCheck:
    kind: str  # "new" | "suppressed" | "existing" | "possible_duplicate"
    job_id: str | None = None
    reason: str = ""


class JobRepository:
    def __init__(self, db: Database):
        self.db = db

    # -- duplicate detection ------------------------------------------------
    def check_duplicate(
        self,
        *,
        canonical_url_hash: str,
        fingerprint: str,
        loose: str,
        has_requisition_id: bool,
        title: str,
        company: str,
        location: str | None,
        country: str | None,
    ) -> DuplicateCheck:
        with self.db.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM suppression_ledger WHERE canonical_url_hash = ? UNION "
                "SELECT 1 FROM suppressed_urls WHERE canonical_url_hash = ?",
                (canonical_url_hash, canonical_url_hash),
            ).fetchone():
                return DuplicateCheck("suppressed", reason="listing URL previously processed")
            row = conn.execute("SELECT job_id FROM jobs WHERE canonical_url_hash = ?", (canonical_url_hash,)).fetchone()
            if row:
                return DuplicateCheck("existing", row[0], "same listing URL already tracked")
            row = conn.execute("SELECT fingerprint FROM suppression_ledger WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if row:
                self._remember_suppressed_url(conn, canonical_url_hash, fingerprint)
                return DuplicateCheck("suppressed", reason="job fingerprint previously processed")
            row = conn.execute("SELECT job_id FROM jobs WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if row:
                return DuplicateCheck("existing", row[0], "same job already tracked (fingerprint)")
            for supp in conn.execute(
                "SELECT fingerprint, has_requisition_id FROM suppression_ledger WHERE loose_key = ?", (loose,)
            ).fetchall():
                if not (supp["has_requisition_id"] and has_requisition_id):
                    self._remember_suppressed_url(conn, canonical_url_hash, supp["fingerprint"])
                    return DuplicateCheck("suppressed", reason="same title/company/location previously processed")
            for job in conn.execute("SELECT job_id, requisition_id FROM jobs WHERE loose_key = ?", (loose,)).fetchall():
                if not (job["requisition_id"] and has_requisition_id):
                    return DuplicateCheck("existing", job["job_id"], "same title/company/location already tracked")
            # Cautious fuzzy check: never auto-merge, route to duplicate review.
            norm_company = normalize_company(company)
            norm_loc = normalize_location(location, country)
            for job in conn.execute(
                "SELECT job_id, title, company, location, country, requisition_id FROM jobs"
                " WHERE status IN ('pending','selected','applied')"
            ).fetchall():
                if job["requisition_id"] and has_requisition_id:
                    continue  # both carry explicit requisition ids and fingerprints differ -> different jobs
                if normalize_company(job["company"]) != norm_company:
                    continue
                if normalize_location(job["location"], job["country"]) != norm_loc:
                    continue
                if title_similarity(job["title"], title) >= FUZZY_TITLE_THRESHOLD:
                    return DuplicateCheck("possible_duplicate", job["job_id"], "very similar title at same employer/location")
        return DuplicateCheck("new")

    @staticmethod
    def _remember_suppressed_url(conn, canonical_url_hash: str, fingerprint: str) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO suppressed_urls(canonical_url_hash, fingerprint) VALUES (?,?)",
            (canonical_url_hash, fingerprint),
        )

    # -- writes ---------------------------------------------------------------
    def insert(self, job: dict[str, Any]) -> str:
        job = dict(job)
        job.setdefault("job_id", uuid.uuid4().hex)
        now = utcnow_iso()
        job.setdefault("ingested_at", now)
        job.setdefault("last_seen_at", now)
        job["updated_at"] = now
        for key in ("matched_skills", "match_details"):
            if key in job and not isinstance(job[key], str) and job[key] is not None:
                job[key] = json.dumps(job[key])
        job["is_test_fixture"] = 1 if job.get("is_test_fixture") else 0
        cols = ", ".join(job.keys())
        marks = ", ".join("?" for _ in job)
        with self.db.transaction() as conn:
            conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", tuple(job.values()))
            conn.execute(
                "INSERT OR IGNORE INTO job_sources(job_id, source_name, source_url, discovered_at) VALUES (?,?,?,?)",
                (job["job_id"], job["source_name"], job["source_url"], now),
            )
        return job["job_id"]

    def add_source(self, job_id: str, source_name: str, source_url: str) -> None:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO job_sources(job_id, source_name, source_url, discovered_at) VALUES (?,?,?,?)",
                (job_id, source_name, source_url, now),
            )
            conn.execute("UPDATE jobs SET last_seen_at = ? WHERE job_id = ?", (now, job_id))

    # -- reads ----------------------------------------------------------------
    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            sources = conn.execute(
                "SELECT source_name, source_url, discovered_at FROM job_sources WHERE job_id = ? ORDER BY discovered_at",
                (job_id,),
            ).fetchall()
        return _decode_job(dict(row), [dict(s) for s in sources])

    def list_by_status(self, status: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY COALESCE(match_score, 0) DESC, ingested_at DESC", (status,)
            ).fetchall()
            out = []
            for row in rows:
                sources = conn.execute(
                    "SELECT source_name, source_url, discovered_at FROM job_sources WHERE job_id = ?", (row["job_id"],)
                ).fetchall()
                out.append(_decode_job(dict(row), [dict(s) for s in sources]))
        return out

    def list_selected(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT j.*, s.selected_at, s.status AS selected_status, s.application_notes FROM jobs j "
                "JOIN selected_jobs s ON s.job_id = j.job_id WHERE j.status = 'selected' ORDER BY s.selected_at DESC"
            ).fetchall()
        return [_decode_job(dict(r), []) for r in rows]

    def list_applied_visible(self, now_iso: str | None = None) -> list[dict[str, Any]]:
        now_iso = now_iso or utcnow_iso()
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM applied_jobs WHERE expires_at > ? ORDER BY applied_at DESC", (now_iso,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["matched_skills"] = json.loads(d.get("matched_skills") or "[]")
            out.append(d)
        return out

    def counts(self) -> dict[str, int]:
        now = utcnow_iso()
        with self.db.connect() as conn:
            by_status = dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
            applied_visible = conn.execute("SELECT COUNT(*) FROM applied_jobs WHERE expires_at > ?", (now,)).fetchone()[0]
            applied_total = conn.execute("SELECT COUNT(*) FROM applied_jobs").fetchone()[0]
            ignored = conn.execute(
                "SELECT COUNT(*) FROM suppression_ledger WHERE suppression_reason = 'ignored'"
            ).fetchone()[0]
            discovered = conn.execute("SELECT COALESCE(SUM(jobs_discovered),0) FROM search_runs").fetchone()[0]
        return {
            "discovered": int(discovered),
            "pending": int(by_status.get("pending", 0)),
            "duplicate_review": int(by_status.get("duplicate_review", 0)),
            "selected": int(by_status.get("selected", 0)),
            "applied_visible": int(applied_visible),
            "applied_total": int(applied_total),
            "ignored": int(ignored),
        }


def _decode_job(row: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    row["matched_skills"] = json.loads(row.get("matched_skills") or "[]")
    row["match_details"] = json.loads(row.get("match_details") or "{}")
    row["is_test_fixture"] = bool(row.get("is_test_fixture"))
    row["sources"] = sources
    return row


# ---------------------------------------------------------------------------
# Runs and logs
# ---------------------------------------------------------------------------


class RunRepository:
    def __init__(self, db: Database):
        self.db = db

    def start(self) -> str:
        run_id = utcnow().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO search_runs(run_id, started_at, status) VALUES (?,?, 'running')", (run_id, utcnow_iso()))
        return run_id

    def finish(self, run_id: str, *, status: str, sources_checked: list[str], discovered: int, eligible: int,
               rejected: int, duplicates: int, errors: list[str]) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE search_runs SET finished_at = ?, status = ?, sources_checked = ?, jobs_discovered = ?,"
                " jobs_eligible = ?, jobs_rejected = ?, jobs_duplicate = ?, error_summary = ? WHERE run_id = ?",
                (utcnow_iso(), status, json.dumps(sources_checked), discovered, eligible, rejected, duplicates,
                 "\n".join(errors)[:4000] or None, run_id),
            )

    def mark_interrupted_runs(self) -> int:
        """Called at startup: runs left 'running' by a crash/restart are marked interrupted."""
        with self.db.transaction() as conn:
            cur = conn.execute(
                "UPDATE search_runs SET status = 'interrupted', finished_at = ?,"
                " error_summary = COALESCE(error_summary, 'Run interrupted (application restarted).')"
                " WHERE status = 'running'",
                (utcnow_iso(),),
            )
            return cur.rowcount

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM search_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def last_finished(self) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM search_runs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def log_listing(self, run_id: str, *, source_name: str, title: str, company: str, url: str, outcome: str,
                    reason: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO search_log(run_id, source_name, title, company, url, outcome, reason, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (run_id, source_name, title[:200], company[:200], url[:500], outcome, reason[:500], utcnow_iso()),
            )

    def search_log(self, run_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            if run_id:
                rows = conn.execute(
                    "SELECT * FROM search_log WHERE run_id = ? ORDER BY id DESC LIMIT ?", (run_id, limit)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM search_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def log(self, run_id: str | None, node: str, level: str, message: str) -> None:
        from config.logging_config import redact

        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO agent_logs(run_id, node, level, message, created_at) VALUES (?,?,?,?,?)",
                (run_id, node, level, redact(message)[:2000], utcnow_iso()),
            )

    def agent_logs(self, run_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            if run_id:
                rows = conn.execute(
                    "SELECT * FROM agent_logs WHERE run_id = ? ORDER BY id DESC LIMIT ?", (run_id, limit)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM agent_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


class HttpCacheRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, max_age_seconds: int) -> str | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT body, fetched_at FROM http_cache WHERE cache_key = ?", (key,)).fetchone()
        if not row:
            return None
        fetched = parse_iso(row["fetched_at"])
        if fetched is None or utcnow() - fetched > timedelta(seconds=max_age_seconds):
            return None
        return row["body"]

    def put(self, key: str, body: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO http_cache(cache_key, body, fetched_at) VALUES (?,?,?)"
                " ON CONFLICT(cache_key) DO UPDATE SET body = excluded.body, fetched_at = excluded.fetched_at",
                (key, body, utcnow_iso()),
            )


def applied_expiry(ingested_at: str) -> str:
    base = parse_iso(ingested_at) or utcnow()
    return (base + timedelta(days=APPLIED_VISIBILITY_DAYS)).isoformat(timespec="seconds")
