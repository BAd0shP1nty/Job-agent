"""Jooble connector (mocked HTTP) and the 'add a job you found' feature."""
from datetime import date

import pytest

from agent.manual import ManualJobError, build_manual_listing, screen_manual_job, source_label
from database.repository import JobRepository, ProfileRepository, SourceRegistryRepository, UsageTracker
from discovery.api_connectors import JOOBLE_LIFETIME_LIMIT, JoobleConnector
from discovery.base import SearchQuery, SourceNotConfigured, SourceQuotaExhausted
from rag.resume_parser import extract_profile, parse_resume
from tests.test_agent_graph import make_deps
from tests.test_connectors import FakeResponse, client

QUERY = SearchQuery(keywords=["service delivery manager", "itsm lead", "sre manager", "ai program manager"],
                    skills=["ITIL", "Service Delivery"], max_results=20)
PAYLOAD = {"totalCount": 2, "jobs": [
    {"id": 1, "title": "Service Delivery Manager", "location": "Bengaluru, Karnataka", "company": "Acme India",
     "snippet": "Lead <b>ITIL</b> service delivery for enterprise clients.", "salary": "", "source": "naukri.com",
     "type": "Full-time", "link": "https://in.jooble.org/jdp/111", "updated": "2026-09-20T10:00:00.0000000"},
    {"id": 2, "title": "Service Delivery Lead", "location": "Pune", "company": "",
     "snippet": "Service delivery role.", "link": "https://in.jooble.org/jdp/222", "updated": "2026-09-20T10:00:00"},
]}


# --------------------------------------------------------------------------- Jooble

def jooble(db, config=None, routes=None):
    http = client(routes or [("in.jooble.org", FakeResponse(200, PAYLOAD))], db=db)
    return JoobleConnector(http, config or {"countries": "in"}, usage=UsageTracker(db, "jooble")), http


def test_jooble_requires_key(db, monkeypatch):
    monkeypatch.delenv("JOOBLE_API_KEY_IN", raising=False)
    conn, _ = jooble(db)
    with pytest.raises(SourceNotConfigured, match="JOOBLE_API_KEY_IN"):
        conn.check_configuration()


def test_jooble_parsing_and_key_not_leaked(db, monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY_IN", "secret-key-123")
    conn, http = jooble(db, {"countries": "in", "requests_per_run": 1})
    listings = conn.search(QUERY)
    assert [l.title for l in listings] == ["Service Delivery Manager"]  # anonymous company dropped
    l = listings[0]
    assert l.country == "India" and l.posting_date is None
    assert l.description == "Lead ITIL service delivery for enterprise clients."
    url, body = http.session.calls[0]
    assert url == "https://in.jooble.org/api/secret-key-123"
    assert body["location"] == "India" and body["keywords"] == "service delivery manager"


def test_jooble_budget_and_cache_protect_quota(db, monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY_IN", "k")
    conn, http = jooble(db, {"countries": "in", "requests_per_run": 2, "locations": "Bangalore, Hyderabad"})
    conn.search(QUERY)
    assert len(http.session.calls) == 2  # budget respected
    assert UsageTracker(db, "jooble").used("in") == 2
    conn.search(QUERY)  # same requests -> served from cache, no quota spent
    assert len(http.session.calls) == 2
    assert UsageTracker(db, "jooble").used("in") == 2


def test_jooble_stops_before_lifetime_limit(db, monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY_IN", "k")
    UsageTracker(db, "jooble").record("in", JOOBLE_LIFETIME_LIMIT - 10)
    conn, http = jooble(db)
    with pytest.raises(SourceQuotaExhausted):
        conn.search(QUERY)
    assert http.session.calls == []


def test_jooble_errors_do_not_reveal_key(db, monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY_IN", "secret-key-123")
    conn, _ = jooble(db, routes=[("in.jooble.org", FakeResponse(404))])
    with pytest.raises(Exception) as exc:
        conn.search(QUERY)
    assert "secret-key-123" not in str(exc.value)


def test_jooble_is_not_retried(db, monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY_IN", "k")
    conn, http = jooble(db, {"countries": "in", "requests_per_run": 1},
                        routes=[("in.jooble.org", [FakeResponse(503)])])
    with pytest.raises(Exception):
        conn.search(QUERY)
    assert len(http.session.calls) == 1  # a retry would cost quota


# --------------------------------------------------------------------------- manual jobs

DESC_INDIA = ("We are looking for a Service Delivery Manager to own ITIL-aligned incident management and change "
              "governance for a telecom client using ServiceNow. Hybrid working, 3 days in our Bengaluru office.")
DESC_UK = ("Service Delivery Manager for our London operations team running ITIL processes. Hybrid working in "
           "London. Candidates must already have the right to work in the UK.")


def manual(url="https://www.linkedin.com/jobs/view/4001?trackingId=abc", title="Service Delivery Manager",
           company="Example Telecom", location="Bengaluru, Karnataka, India", description=DESC_INDIA, **kw):
    return build_manual_listing(url=url, title=title, company=company, location=location, description=description,
                                **kw)


def with_resume(db, sample_resume_bytes):
    parsed = parse_resume("resume.docx", sample_resume_bytes)
    ProfileRepository(db).add("resume.docx", extract_profile(parsed), parsed.text)


def test_manual_validation():
    with pytest.raises(ManualJobError, match="link"):
        manual(url="linkedin.com/jobs/1")
    with pytest.raises(ManualJobError, match="title"):
        manual(title=" ")
    with pytest.raises(ManualJobError, match="full job description"):
        manual(description="Too short")
    assert source_label("https://in.linkedin.com/jobs/view/1") == "manual:linkedin.com"
    assert source_label("https://www.naukri.com/job-listings-x") == "manual:naukri.com"


def test_manual_eligible_job_goes_to_pending(db, sample_resume_bytes):
    with_resume(db, sample_resume_bytes)
    deps = make_deps(db, enabled=())
    result = screen_manual_job(deps, manual(posting_date=date(2024, 1, 1)))  # old date is not filtered for manual
    assert result.outcome == "pending", result.reason
    job = JobRepository(db).get(result.job_id)
    assert job["source_name"] == "manual:linkedin.com" and job["is_test_fixture"] is False
    assert "ITIL" in job["matched_skills"]
    assert job["match_details"]["candidate_evidence"]  # grounded in the resume


def test_manual_ineligible_job_explains_why(db):
    result = screen_manual_job(make_deps(db, enabled=()), manual(
        url="https://www.linkedin.com/jobs/view/4002", location="London, United Kingdom", description=DESC_UK))
    assert result.outcome == "rejected" and "Overseas role" in result.reason
    assert result.job_id is None


def test_manual_duplicate_and_processed_jobs(db):
    deps = make_deps(db, enabled=())
    first = screen_manual_job(deps, manual())
    again = screen_manual_job(deps, manual(url="https://www.linkedin.com/jobs/view/4001?refId=zzz"))
    assert first.outcome == "pending" and again.outcome == "duplicate"
    # Same job pasted from Naukri after it was ignored -> stays suppressed.
    from database.lifecycle import JobLifecycleService

    JobLifecycleService(db).ignore(first.job_id)
    naukri = screen_manual_job(deps, manual(url="https://www.naukri.com/job-listings-sdm-777",
                                            title="Service Delivery Manager (Hybrid)"))
    assert naukri.outcome == "duplicate"


def test_manual_run_does_not_call_sources(db):
    deps = make_deps(db, enabled=("test_fixtures",))
    screen_manual_job(deps, manual())
    assert len(JobRepository(db).list_by_status("pending")) == 1  # fixtures were not searched
    reg = {r["source_name"]: r for r in SourceRegistryRepository(db).list()}
    assert reg["test_fixtures"]["last_checked_at"] is None
