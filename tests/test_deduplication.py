from tests.helpers import insert_job

from database.lifecycle import JobLifecycleService
from database.repository import JobRepository
from screening.deduplication import (
    canonicalize_url,
    job_fingerprint,
    loose_key,
    normalize_company,
    normalize_title,
    url_hash,
)


def check(db, *, title, company, location, country="India", url, requisition_id=None):
    return JobRepository(db).check_duplicate(
        canonical_url_hash=url_hash(url), fingerprint=job_fingerprint(title, company, location, country, requisition_id),
        loose=loose_key(title, company, location, country), has_requisition_id=bool(requisition_id), title=title,
        company=company, location=location, country=country)


def test_tracking_parameters_are_removed():
    a = canonicalize_url("https://www.Careers.Example.com/jobs/123/?utm_source=linkedin&utm_medium=x&gclid=1#apply")
    b = canonicalize_url("http://careers.example.com/jobs/123?trk=feed&refId=abc")
    assert a == b == "https://careers.example.com/jobs/123"


def test_meaningful_query_parameters_are_kept_and_sorted():
    a = canonicalize_url("https://example.com/view?jk=abc&utm_campaign=x&gh_jid=42")
    b = canonicalize_url("https://example.com/view?gh_jid=42&jk=abc")
    assert a == b and "jk=abc" in a and "gh_jid=42" in a
    assert canonicalize_url("https://example.com/view?jk=abc") != canonicalize_url("https://example.com/view?jk=def")


def test_regional_linkedin_urls_collapse():
    assert canonicalize_url("https://in.linkedin.com/jobs/view/123?trackingId=x") == \
        canonicalize_url("https://www.linkedin.com/jobs/view/123")


def test_title_and_company_normalization():
    assert normalize_title("Sr. Service Delivery Mgr (Remote)") == normalize_title("Senior Service Delivery Manager")
    assert normalize_title("SRE – Lead (m/f/d)") == normalize_title("SRE Lead")
    assert normalize_company("Example Telecom Pvt. Ltd.") == normalize_company("Example Telecom Private Limited")


def test_fingerprint_distinguishes_different_jobs_at_same_employer():
    base = dict(company="Example Co", location="Bangalore", country="India")
    assert job_fingerprint("Service Delivery Manager", **base) != job_fingerprint("SRE Manager", **base)
    assert job_fingerprint("SRE Manager", **base) != job_fingerprint("SRE Manager", company="Example Co",
                                                                    location="Pune", country="India")
    assert job_fingerprint("SRE Manager", **base, requisition_id="R1") != \
        job_fingerprint("SRE Manager", **base, requisition_id="R2")
    assert job_fingerprint("SRE Manager", company="Example Co", location="Bengaluru, Karnataka", country="India") == \
        job_fingerprint("SRE Manager", **base)


def test_same_job_on_another_portal_is_existing(db):
    job_id = insert_job(db, url="https://careers.example.com/jobs/1?utm_source=x")
    res = check(db, title="Service Delivery Manager (Hybrid)", company="[TEST FIXTURE] Example Co Ltd",
                location="Bengaluru", url="https://jobs.example.org/view/abc?ref=feed")
    assert res.kind == "existing" and res.job_id == job_id


def test_tracking_url_variant_is_existing(db):
    job_id = insert_job(db)
    res = check(db, title="Totally different title", company="Other", location="Pune",
                url="https://careers.example.com/jobs/1?utm_campaign=abc&gclid=9")
    assert res.kind == "existing" and res.job_id == job_id


def test_distinct_requisitions_are_new_jobs(db):
    insert_job(db, requisition_id="REQ-1")
    res = check(db, title="Service Delivery Manager", company="[TEST FIXTURE] Example Co", location="Bangalore",
                url="https://careers.example.com/jobs/2", requisition_id="REQ-2")
    assert res.kind == "new"


def test_ignored_job_is_suppressed_from_any_portal(db):
    job_id = insert_job(db)
    JobLifecycleService(db).ignore(job_id)
    assert JobRepository(db).get(job_id) is None
    for url, title in [("https://careers.example.com/jobs/1?utm_source=feed", "Service Delivery Manager"),
                       ("https://another-board.example.net/x/99", "Service Delivery Manager (Remote)"),
                       ("https://another-board.example.net/x/100", "service delivery manager")]:
        res = check(db, title=title, company="[TEST FIXTURE] Example Co", location="Bangalore", url=url)
        assert res.kind == "suppressed", url


def test_similar_but_uncertain_title_goes_to_review_not_deletion(db):
    insert_job(db, title="AI Program Manager", location="Hyderabad")
    res = check(db, title="AI Programme Manager", company="[TEST FIXTURE] Example Co", location="Hyderabad",
                url="https://careers.example.com/jobs/77")
    assert res.kind == "possible_duplicate"


def test_different_job_same_employer_is_not_suppressed(db):
    job_id = insert_job(db)
    JobLifecycleService(db).ignore(job_id)
    res = check(db, title="Head of SRE", company="[TEST FIXTURE] Example Co", location="Bangalore",
                url="https://careers.example.com/jobs/555")
    assert res.kind == "new"


def test_suppression_ledger_stores_only_minimal_data(db):
    job_id = insert_job(db)
    JobLifecycleService(db).ignore(job_id)
    with db.connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(suppression_ledger)")]
        row = dict(conn.execute("SELECT * FROM suppression_ledger").fetchone())
    assert set(cols) == {"fingerprint", "canonical_url_hash", "loose_key", "has_requisition_id", "source_name",
                         "suppressed_at", "suppression_reason"}
    assert row["suppression_reason"] == "ignored"
    assert "Service Delivery" not in str(row) and "example.com" not in str(row)
