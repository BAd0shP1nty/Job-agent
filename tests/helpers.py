"""Test helpers (mock data only)."""
from __future__ import annotations


def insert_job(db, *, title="Service Delivery Manager", company="[TEST FIXTURE] Example Co",
               location="Bangalore, India", country="India", url="https://careers.example.com/jobs/1",
               ingested_at=None, status="pending", requisition_id=None, source_name="test_fixtures"):
    """Insert a mock job with correctly derived dedup keys; returns job_id."""
    from database.repository import JobRepository
    from screening.deduplication import canonicalize_url, job_fingerprint, loose_key, sha256

    canonical = canonicalize_url(url)
    row = {
        "canonical_url": canonical, "canonical_url_hash": sha256(canonical),
        "fingerprint": job_fingerprint(title, company, location, country, requisition_id),
        "loose_key": loose_key(title, company, location, country), "requisition_id": requisition_id,
        "title": title, "company": company, "location": location, "country": country,
        "work_arrangement": "hybrid", "job_description": "Mock description mentioning ITIL.",
        "source_name": source_name, "source_url": url, "eligibility_status": "eligible",
        "match_summary": "mock", "match_score": 50.0, "matched_skills": ["ITIL"], "match_details": {},
        "is_test_fixture": True, "status": status,
    }
    if ingested_at:
        row["ingested_at"] = ingested_at
    return JobRepository(db).insert(row)
