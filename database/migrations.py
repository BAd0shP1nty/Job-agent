"""Versioned schema migrations.

Each migration runs once, inside a transaction, and is recorded in
``schema_migrations``. Add new migrations by appending to ``MIGRATIONS``.
"""
from __future__ import annotations

from config.settings import utcnow_iso
from database.connection import Database

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial schema",
        """
        CREATE TABLE candidate_profiles (
            profile_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            resume_filename   TEXT NOT NULL,
            resume_version    INTEGER NOT NULL,
            extracted_profile TEXT NOT NULL,          -- JSON (reviewed profile)
            resume_text       TEXT NOT NULL,          -- extracted text, local only
            active            INTEGER NOT NULL DEFAULT 0,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        );
        CREATE UNIQUE INDEX ux_one_active_profile ON candidate_profiles(active) WHERE active = 1;

        CREATE TABLE jobs (
            job_id                    TEXT PRIMARY KEY,
            canonical_url             TEXT NOT NULL UNIQUE,
            canonical_url_hash        TEXT NOT NULL UNIQUE,
            fingerprint               TEXT NOT NULL UNIQUE,
            loose_key                 TEXT NOT NULL,
            requisition_id            TEXT,
            title                     TEXT NOT NULL,
            company                   TEXT NOT NULL,
            location                  TEXT,
            country                   TEXT,
            work_arrangement          TEXT,
            employment_type           TEXT,
            seniority                 TEXT,
            experience_required       TEXT,
            job_description           TEXT,
            posting_date              TEXT,
            source_name               TEXT NOT NULL,
            source_url                TEXT NOT NULL,
            ingested_at               TEXT NOT NULL,
            last_seen_at              TEXT NOT NULL,
            eligibility_status        TEXT NOT NULL,
            remote_eligibility        TEXT,
            relocation_evidence       TEXT,
            visa_sponsorship_evidence TEXT,
            evidence_source_url       TEXT,
            match_summary             TEXT,
            match_score               REAL,
            matched_skills            TEXT,           -- JSON array
            match_details             TEXT,           -- JSON (MatchResult)
            confidence                TEXT,
            duplicate_of              TEXT,
            is_test_fixture           INTEGER NOT NULL DEFAULT 0,
            status                    TEXT NOT NULL CHECK (status IN ('pending','duplicate_review','selected','applied')),
            updated_at                TEXT NOT NULL
        );
        CREATE INDEX ix_jobs_status ON jobs(status);
        CREATE INDEX ix_jobs_loose ON jobs(loose_key);

        CREATE TABLE job_sources (
            job_id        TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            source_name   TEXT NOT NULL,
            source_url    TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            PRIMARY KEY (job_id, source_url)
        );

        CREATE TABLE selected_jobs (
            job_id            TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
            selected_at       TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'Selected',
            application_notes TEXT
        );

        CREATE TABLE applied_jobs (
            job_id             TEXT PRIMARY KEY,
            title              TEXT NOT NULL,
            company            TEXT NOT NULL,
            location           TEXT,
            source_name        TEXT,
            source_url         TEXT NOT NULL,
            matched_skills     TEXT,
            ingested_at        TEXT NOT NULL,
            applied_at         TEXT NOT NULL,
            application_status TEXT NOT NULL,
            expires_at         TEXT NOT NULL,
            is_test_fixture    INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX ix_applied_expiry ON applied_jobs(expires_at);

        -- Minimal ledger: hashes only, no job content.
        CREATE TABLE suppression_ledger (
            fingerprint        TEXT PRIMARY KEY,
            canonical_url_hash TEXT NOT NULL,
            loose_key          TEXT NOT NULL,
            has_requisition_id INTEGER NOT NULL DEFAULT 0,
            source_name        TEXT NOT NULL,
            suppressed_at      TEXT NOT NULL,
            suppression_reason TEXT NOT NULL
        );
        CREATE INDEX ix_supp_url ON suppression_ledger(canonical_url_hash);
        CREATE INDEX ix_supp_loose ON suppression_ledger(loose_key);

        -- Extra canonical URL hashes for suppressed jobs seen on several portals.
        CREATE TABLE suppressed_urls (
            canonical_url_hash TEXT PRIMARY KEY,
            fingerprint        TEXT NOT NULL
        );

        CREATE TABLE search_runs (
            run_id          TEXT PRIMARY KEY,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            status          TEXT NOT NULL DEFAULT 'running',
            sources_checked TEXT,                     -- JSON array
            jobs_discovered INTEGER NOT NULL DEFAULT 0,
            jobs_eligible   INTEGER NOT NULL DEFAULT 0,
            jobs_rejected   INTEGER NOT NULL DEFAULT 0,
            jobs_duplicate  INTEGER NOT NULL DEFAULT 0,
            error_summary   TEXT
        );

        -- Internal search log: concise outcome per screened listing (no descriptions).
        CREATE TABLE search_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL,
            source_name TEXT,
            title       TEXT,
            company     TEXT,
            url         TEXT,
            outcome     TEXT NOT NULL,              -- rejected / unverified / duplicate / flagged / pending
            reason      TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX ix_search_log_run ON search_log(run_id);

        CREATE TABLE agent_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id     TEXT,
            node       TEXT,
            level      TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE source_registry (
            source_name      TEXT PRIMARY KEY,
            enabled          INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'untested',
            last_success_at  TEXT,
            last_checked_at  TEXT,
            last_error       TEXT,
            failure_count    INTEGER NOT NULL DEFAULT 0,
            config           TEXT                     -- JSON (non-secret connector config)
        );

        CREATE TABLE app_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE http_cache (
            cache_key  TEXT PRIMARY KEY,
            body       TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        );
        """,
    ),
]


def migrate(db: Database) -> int:
    """Apply pending migrations; returns the resulting schema version."""
    with db.transaction() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for version, name, sql in MIGRATIONS:
            if version in applied:
                continue
            for statement in [s.strip() for s in sql.split(";") if s.strip()]:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?,?,?)",
                (version, name, utcnow_iso()),
            )
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)
