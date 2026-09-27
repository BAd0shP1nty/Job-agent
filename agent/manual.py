"""Screen a job the user found themselves (e.g. on LinkedIn or Naukri) and pasted into the app.

Nothing is fetched from the job's URL: the portal pages are not scraped. The user supplies
the text, and the job then goes through exactly the same rules, evidence retrieval,
validation, deduplication and lifecycle as discovered jobs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from urllib.parse import urlsplit

from agent.graph import run_search
from agent.nodes import AgentDeps
from config.settings import utcnow_iso
from database.models import RawListing
from database.repository import RunRepository
from screening.text_utils import sanitize_text

MIN_DESCRIPTION_CHARS = 150


class ManualJobError(ValueError):
    pass


@dataclass
class ManualJobResult:
    outcome: str          # pending | duplicate_review | rejected | unverified | duplicate | flagged | below_threshold
    reason: str
    job_id: str | None
    run_id: str


def source_label(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    if host.endswith(".linkedin.com"):
        host = "linkedin.com"
    return f"manual:{host or 'unknown'}"


def build_manual_listing(*, url: str, title: str, company: str, location: str, description: str,
                         work_arrangement: str | None = None, posting_date: date | None = None,
                         employment_type: str | None = None) -> RawListing:
    url = (url or "").strip()
    if not url.lower().startswith(("https://", "http://")) or not urlsplit(url).netloc:
        raise ManualJobError("Enter the full job link, starting with https://")
    if not (title or "").strip():
        raise ManualJobError("Enter the job title.")
    if not (company or "").strip():
        raise ManualJobError("Enter the company name.")
    text = sanitize_text(description)
    if len(text) < MIN_DESCRIPTION_CHARS:
        raise ManualJobError(f"Paste the full job description (at least {MIN_DESCRIPTION_CHARS} characters) so skills, "
                             "location and visa/relocation terms can be checked against real text.")
    hint = work_arrangement if work_arrangement in ("remote", "hybrid", "onsite") else None
    posted = (datetime.combine(posting_date, time(0, 0), tzinfo=timezone.utc).isoformat(timespec="seconds")
              if posting_date else None)
    return RawListing(
        source_name=source_label(url), source_url=url, title=sanitize_text(title, 300),
        company=sanitize_text(company, 200), location=sanitize_text(location or "", 200), description=text,
        posting_date=posted, work_arrangement_hint=hint, employment_type=employment_type or None,
        retrieved_at=utcnow_iso(), is_test_fixture=False,
    )


def screen_manual_job(deps: AgentDeps, listing: RawListing) -> ManualJobResult:
    final = run_search(deps, overrides={
        "manual_listings": [listing.model_dump()],
        "min_relevance_score": 0,   # the user chose this job; don't hide it behind the score threshold
        "date_posted_days": None,   # nor behind the date filter
    })
    run_id = final["run_id"]
    if final.get("fatal"):
        return ManualJobResult("rejected", "; ".join(final.get("errors") or ["the run failed"]), None, run_id)
    entries = RunRepository(deps.db).search_log(run_id)
    entry = next((e for e in entries if e["url"] == listing.source_url[:500]), entries[0] if entries else None)
    saved = final.get("saved_job_ids") or []
    if entry is None:
        return ManualJobResult("rejected", "No outcome was recorded for this job.", None, run_id)
    return ManualJobResult(entry["outcome"], entry["reason"] or "", saved[0] if saved else None, run_id)
