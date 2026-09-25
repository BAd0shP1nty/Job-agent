from datetime import timedelta

import pytest
from tests.helpers import insert_job

from config.settings import parse_iso, utcnow
from database.connection import Database
from database.lifecycle import JobLifecycleService, LifecycleError
from database.migrations import migrate
from database.repository import JobRepository, RunRepository, SettingsRepository


def test_mark_relevant_moves_job_to_selected(db):
    job_id = insert_job(db)
    JobLifecycleService(db).mark_relevant(job_id)
    repo = JobRepository(db)
    assert repo.list_by_status("pending") == []
    selected = repo.list_selected()
    assert [j["job_id"] for j in selected] == [job_id]
    assert selected[0]["source_url"].startswith("https://")  # direct link available on every selected record


def test_ignore_deletes_active_record_and_writes_fingerprint(db):
    job_id = insert_job(db)
    JobLifecycleService(db).ignore(job_id)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM suppression_ledger").fetchone()[0] == 1


def test_applied_moves_out_of_selected_with_expiry_from_ingestion(db):
    ingested = (utcnow() - timedelta(days=5)).isoformat(timespec="seconds")
    job_id = insert_job(db, ingested_at=ingested)
    svc = JobLifecycleService(db)
    svc.mark_relevant(job_id)
    expires = svc.mark_applied(job_id)
    repo = JobRepository(db)
    assert repo.list_selected() == []
    applied = repo.list_applied_visible()
    assert len(applied) == 1 and applied[0]["application_status"] == "Applied"
    # 15 days from original ingestion, not from the applied timestamp.
    assert parse_iso(expires) == parse_iso(ingested) + timedelta(days=15)
    assert parse_iso(applied[0]["applied_at"]) > parse_iso(ingested)


def test_applied_record_visible_within_15_days_and_hidden_after(db):
    ingested = (utcnow() - timedelta(days=14)).isoformat(timespec="seconds")
    job_id = insert_job(db, ingested_at=ingested)
    svc = JobLifecycleService(db)
    svc.mark_relevant(job_id)
    svc.mark_applied(job_id)
    repo = JobRepository(db)
    assert len(repo.list_applied_visible()) == 1
    two_days_later = (utcnow() + timedelta(days=2)).isoformat(timespec="seconds")
    assert repo.list_applied_visible(now_iso=two_days_later) == []


def test_expired_applied_record_is_cleaned_up_but_stays_suppressed(db):
    ingested = (utcnow() - timedelta(days=16)).isoformat(timespec="seconds")
    job_id = insert_job(db, ingested_at=ingested)
    svc = JobLifecycleService(db)
    svc.mark_relevant(job_id)
    svc.mark_applied(job_id)
    repo = JobRepository(db)
    assert repo.list_applied_visible() == []  # query-based expiry
    assert svc.cleanup_expired_applied() == 1
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        reasons = [r[0] for r in conn.execute("SELECT suppression_reason FROM suppression_ledger")]
    assert reasons == ["applied_expired"]
    from tests.test_deduplication import check

    res = check(db, title="Service Delivery Manager", company="[TEST FIXTURE] Example Co", location="Bangalore",
                url="https://careers.example.com/jobs/1")
    assert res.kind == "suppressed"


def test_invalid_transitions_are_rejected(db):
    svc = JobLifecycleService(db)
    job_id = insert_job(db)
    with pytest.raises(LifecycleError):
        svc.mark_applied(job_id)  # must be selected first
    svc.mark_relevant(job_id)
    with pytest.raises(LifecycleError):
        svc.mark_relevant(job_id)  # double click
    with pytest.raises(LifecycleError):
        svc.ignore(job_id)  # selected jobs are not in the pending queue
    with pytest.raises(LifecycleError):
        svc.ignore("does-not-exist")


def test_job_is_never_in_two_states(db):
    svc = JobLifecycleService(db)
    ids = [insert_job(db, title=f"Role {i}", url=f"https://careers.example.com/jobs/{i}") for i in range(3)]
    svc.mark_relevant(ids[0])
    svc.mark_relevant(ids[1])
    svc.mark_applied(ids[1])
    with db.connect() as conn:
        statuses = dict(conn.execute("SELECT job_id, status FROM jobs").fetchall())
        selected = {r[0] for r in conn.execute("SELECT job_id FROM selected_jobs")}
        applied = {r[0] for r in conn.execute("SELECT job_id FROM applied_jobs")}
    assert statuses == {ids[0]: "selected", ids[1]: "applied", ids[2]: "pending"}
    assert selected == {ids[0]} and applied == {ids[1]} and not (selected & applied)


def test_transaction_rolls_back_on_failure(db, monkeypatch):
    svc = JobLifecycleService(db)
    job_id = insert_job(db)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(JobLifecycleService, "_suppress", staticmethod(boom))
    with pytest.raises(RuntimeError):
        svc.mark_relevant(job_id)
    job = JobRepository(db).get(job_id)
    assert job["status"] == "pending"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM selected_jobs").fetchone()[0] == 0


def test_state_survives_restart(file_db):
    database, path = file_db
    svc = JobLifecycleService(database)
    a = insert_job(database, title="Role A", url="https://careers.example.com/jobs/a")
    b = insert_job(database, title="Role B", url="https://careers.example.com/jobs/b")
    svc.mark_relevant(a)
    svc.ignore(b)
    SettingsRepository(database).save_search_settings({"bangalore_only": True})
    run_id = RunRepository(database).start()  # simulate a crash mid-run
    database.close()

    reopened = Database(path)
    migrate(reopened)  # idempotent
    assert RunRepository(reopened).mark_interrupted_runs() == 1
    assert [r["status"] for r in RunRepository(reopened).list() if r["run_id"] == run_id] == ["interrupted"]
    assert [j["job_id"] for j in JobRepository(reopened).list_selected()] == [a]
    assert SettingsRepository(reopened).search_settings()["bangalore_only"] is True
    with reopened.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM suppression_ledger").fetchone()[0] == 2
    reopened.close()


def test_changing_search_settings_does_not_touch_jobs(db):
    svc = JobLifecycleService(db)
    a = insert_job(db)
    svc.mark_relevant(a)
    SettingsRepository(db).save_search_settings({"remote_only": True, "target_titles": ["Other"]})
    assert len(JobRepository(db).list_selected()) == 1
