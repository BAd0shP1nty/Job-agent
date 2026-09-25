"""Connectors for public job-search APIs.

Each connector maps the provider's documented JSON schema to ``RawListing``.
Provider data is never enriched with guessed values: missing fields stay empty.
"""
from __future__ import annotations

from datetime import datetime, timezone

from config.settings import get_settings
from database.models import RawListing
from discovery.base import (
    ConnectorInfo,
    SearchQuery,
    SourceAdapter,
    SourceFailure,
    SourceNotConfigured,
    keyword_prefilter,
)
from screening.text_utils import html_to_text, sanitize_text


def _unix_to_iso(value) -> str | None:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:  # milliseconds
        ts /= 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _iso_or_none(value) -> str | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


class RemotiveConnector(SourceAdapter):
    """https://remotive.com/api/remote-jobs - public API for remote jobs.

    Remotive's terms ask API users to link back to the original listing and to
    keep request volume low, so responses are cached for 6 hours and at most
    three keyword queries are made per run.
    """

    info = ConnectorInfo(
        name="remotive",
        display_name="Remotive (public API)",
        kind="official_api",
        description="Remote jobs worldwide. Provides a structured 'candidate_required_location' field.",
        access_notes="Public API, no key. Link back to Remotive listings; low request volume (cached 6h).",
        enabled_by_default=True,
        terms_url="https://remotive.com/api-documentation",
    )
    URL = "https://remotive.com/api/remote-jobs"

    def search(self, query: SearchQuery) -> list[RawListing]:
        listings: dict[str, RawListing] = {}
        for keyword in (query.keywords or ["service delivery"])[:3]:
            data = self.http.get_json(self.URL, {"search": keyword, "limit": query.max_results}, cache_ttl=6 * 3600)
            jobs = data.get("jobs") if isinstance(data, dict) else None
            if jobs is None:
                raise SourceFailure("Remotive response did not contain a 'jobs' array.")
            for job in jobs:
                try:
                    url = job["url"]
                    listings[url] = RawListing(
                        source_name=self.name,
                        source_url=url,
                        title=sanitize_text(job["title"], 300),
                        company=sanitize_text(job.get("company_name") or "", 200),
                        location=sanitize_text(job.get("candidate_required_location") or "", 200),
                        description=html_to_text(job.get("description")),
                        posting_date=_iso_or_none(job.get("publication_date")),
                        work_arrangement_hint="remote",
                        employment_type=job.get("job_type"),
                        remote_location_field=job.get("candidate_required_location"),
                        retrieved_at=self.now(),
                    )
                except (KeyError, TypeError):
                    continue  # malformed listing
        return keyword_prefilter(list(listings.values()), query)


class ArbeitnowConnector(SourceAdapter):
    """https://www.arbeitnow.com/api/job-board-api - public API, mostly Germany / Europe."""

    info = ConnectorInfo(
        name="arbeitnow",
        display_name="Arbeitnow (public API, Germany/EU)",
        kind="official_api",
        description="European (mainly German) job board API with a 'remote' flag.",
        access_notes="Public API, no key. Paginated; first 3 pages scanned and cached for 1h.",
        enabled_by_default=True,
        terms_url="https://www.arbeitnow.com/blog/job-board-api",
    )
    URL = "https://www.arbeitnow.com/api/job-board-api"

    def search(self, query: SearchQuery) -> list[RawListing]:
        results: list[RawListing] = []
        for page in (1, 2, 3):
            data = self.http.get_json(self.URL, {"page": page})
            jobs = data.get("data") if isinstance(data, dict) else None
            if jobs is None:
                raise SourceFailure("Arbeitnow response did not contain a 'data' array.")
            for job in jobs:
                try:
                    results.append(RawListing(
                        source_name=self.name,
                        source_url=job["url"],
                        title=sanitize_text(job["title"], 300),
                        company=sanitize_text(job.get("company_name") or "", 200),
                        location=sanitize_text(job.get("location") or "", 200),
                        description=html_to_text(job.get("description")),
                        posting_date=_unix_to_iso(job.get("created_at")),
                        work_arrangement_hint="remote" if job.get("remote") is True else None,
                        employment_type=", ".join(job.get("job_types") or []) or None,
                        retrieved_at=self.now(),
                    ))
                except (KeyError, TypeError):
                    continue
            if not jobs:
                break
        return keyword_prefilter(results, query)


class HimalayasConnector(SourceAdapter):
    """https://himalayas.app/jobs/api - public remote-jobs API with location restrictions."""

    info = ConnectorInfo(
        name="himalayas",
        display_name="Himalayas (public API, remote)",
        kind="official_api",
        description="Remote jobs with explicit 'locationRestrictions' (countries where candidates may live).",
        access_notes="Public API, no key. Max 20 results per page; 3 pages scanned, cached 1h.",
        enabled_by_default=True,
        terms_url="https://himalayas.app/api",
    )
    URL = "https://himalayas.app/jobs/api"

    def search(self, query: SearchQuery) -> list[RawListing]:
        results: list[RawListing] = []
        for offset in (0, 20, 40):
            data = self.http.get_json(self.URL, {"limit": 20, "offset": offset})
            jobs = data.get("jobs") if isinstance(data, dict) else None
            if jobs is None:
                raise SourceFailure("Himalayas response did not contain a 'jobs' array.")
            for job in jobs:
                try:
                    restrictions = job.get("locationRestrictions") or []
                    names = [r if isinstance(r, str) else (r.get("name") or "") for r in restrictions]
                    names = [n for n in names if n]
                    results.append(RawListing(
                        source_name=self.name,
                        source_url=job.get("applicationLink") or job["guid"],
                        title=sanitize_text(job["title"], 300),
                        company=sanitize_text(job.get("companyName") or "", 200),
                        location=", ".join(names) if names else "Remote",
                        description=html_to_text(job.get("description") or job.get("excerpt")),
                        posting_date=_unix_to_iso(job.get("pubDate")),
                        work_arrangement_hint="remote",
                        employment_type=job.get("employmentType"),
                        seniority=", ".join(job.get("seniority") or []) if isinstance(job.get("seniority"), list)
                        else job.get("seniority"),
                        # Empty restrictions are *not* treated as "worldwide" - unknown stays unknown.
                        remote_location_field=", ".join(names) if names else None,
                        retrieved_at=self.now(),
                    ))
                except (KeyError, TypeError):
                    continue
            if not jobs:
                break
        return keyword_prefilter(results, query)


ADZUNA_COUNTRIES = {"in": "India", "gb": "United Kingdom", "de": "Germany", "fr": "France", "nl": "Netherlands",
                    "at": "Austria", "be": "Belgium", "ch": "Switzerland", "es": "Spain", "it": "Italy",
                    "pl": "Poland", "za": "South Africa"}


class AdzunaConnector(SourceAdapter):
    """https://developer.adzuna.com - official job search API (free key required)."""

    info = ConnectorInfo(
        name="adzuna",
        display_name="Adzuna (official API, key required)",
        kind="official_api",
        description="Aggregated listings for India, UK and major EU countries.",
        access_notes="Requires ADZUNA_APP_ID and ADZUNA_APP_KEY. Descriptions are snippets, so sponsorship/relocation "
                     "evidence is often incomplete and such overseas jobs stay unverified.",
        requires_credentials=True,
        config_fields={"countries": "Comma-separated Adzuna country codes, e.g. in,gb,de,nl,fr"},
        terms_url="https://developer.adzuna.com/overview",
    )

    def check_configuration(self) -> None:
        settings = get_settings()
        if not (settings.adzuna_app_id and settings.adzuna_app_key):
            raise SourceNotConfigured("Set ADZUNA_APP_ID and ADZUNA_APP_KEY in the environment to enable Adzuna.")

    def search(self, query: SearchQuery) -> list[RawListing]:
        self.check_configuration()
        settings = get_settings()
        codes = [c.strip().lower() for c in str(self.config.get("countries", "in,gb,de,nl,ie,fr")).split(",")]
        results: list[RawListing] = []
        for code in [c for c in codes if c in ADZUNA_COUNTRIES or c == "ie"][:6]:
            for keyword in (query.keywords or ["service delivery manager"])[:2]:
                params = {"app_id": settings.adzuna_app_id, "app_key": settings.adzuna_app_key, "what": keyword,
                          "results_per_page": min(query.max_results, 50), "content-type": "application/json"}
                if query.date_posted_days:
                    params["max_days_old"] = query.date_posted_days
                data = self.http.get_json(f"https://api.adzuna.com/v1/api/jobs/{code}/search/1", params)
                for job in (data or {}).get("results", []):
                    try:
                        area = (job.get("location") or {}).get("area") or []
                        results.append(RawListing(
                            source_name=self.name,
                            source_url=job["redirect_url"],
                            title=sanitize_text(job["title"], 300),
                            company=sanitize_text((job.get("company") or {}).get("display_name") or "", 200),
                            location=sanitize_text((job.get("location") or {}).get("display_name") or "", 200),
                            country=ADZUNA_COUNTRIES.get(code) or (area[0] if area else None),
                            description=html_to_text(job.get("description")),
                            posting_date=_iso_or_none(job.get("created")),
                            employment_type=job.get("contract_time"),
                            retrieved_at=self.now(),
                        ))
                    except (KeyError, TypeError):
                        continue
        return keyword_prefilter(results, query)
