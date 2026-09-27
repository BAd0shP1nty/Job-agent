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


JOOBLE_COUNTRIES = {
    "in": ("in.jooble.org", "India"),
    "us": ("jooble.org", "United States"),
    "uk": ("uk.jooble.org", "United Kingdom"),
    "ie": ("ie.jooble.org", "Ireland"),
    "de": ("de.jooble.org", "Germany"),
    "fr": ("fr.jooble.org", "France"),
    "nl": ("nl.jooble.org", "Netherlands"),
    "ae": ("ae.jooble.org", "United Arab Emirates"),
}
JOOBLE_LIFETIME_LIMIT = 500   # free plan: 500 requests per key, lifetime (not monthly)
JOOBLE_SAFETY_MARGIN = 10     # stop this many requests short of the limit


def jooble_key(country: str) -> str | None:
    """Keys are per country and read from the environment, e.g. JOOBLE_API_KEY_IN."""
    import os

    return os.getenv(f"JOOBLE_API_KEY_{country.upper()}") or None


class JoobleConnector(SourceAdapter):
    """Jooble REST API (https://help.jooble.org - REST API Documentation).

    * One key per country domain (a jooble.org key only returns US jobs; register on
      in.jooble.org/api/about for India).
    * Free plan: 500 requests per key for its whole lifetime. This connector counts
      every non-cached request, caches responses for 12 hours, spends at most
      ``requests_per_run`` requests per search, never retries a request (a retry would
      also cost quota), and stops 10 requests before the limit.
    * Results carry only a short snippet, so skill evidence is limited to title + snippet
      and overseas visa/relocation evidence is rarely available.
    * ``updated`` is a last-updated time, not a posting date, so it is not used as one.
    """

    info = ConnectorInfo(
        name="jooble",
        display_name="Jooble (official API, key per country)",
        kind="official_api",
        description="Job aggregator with Indian listings (in.jooble.org). Returns title, company, location and a "
                    "short snippet.",
        access_notes="Free key per country, set as JOOBLE_API_KEY_IN (India), JOOBLE_API_KEY_UK, … in .env. Free "
                     "plan = 500 requests per key in total, so requests are budgeted and cached for 12 h.",
        requires_credentials=True,
        config_fields={
            "countries": "Country codes with a key in .env, comma-separated: in, uk, ie, de, fr, nl, ae, us",
            "locations": "Optional search locations for India, comma-separated (default: India). "
                         "e.g. Bangalore, Hyderabad, Pune",
            "requests_per_run": "Maximum Jooble requests per search run across all countries (default 3)",
        },
        terms_url="https://help.jooble.org/en/support/solutions/articles/60001448238-rest-api-documentation",
    )

    def _countries(self) -> list[str]:
        raw = str(self.config.get("countries") or "in")
        return [c.strip().lower() for c in raw.split(",") if c.strip().lower() in JOOBLE_COUNTRIES]

    def check_configuration(self) -> None:
        countries = self._countries()
        if not countries:
            raise SourceNotConfigured("Set 'countries' for Jooble (e.g. in).")
        if not any(jooble_key(c) for c in countries):
            names = ", ".join(f"JOOBLE_API_KEY_{c.upper()}" for c in countries)
            raise SourceNotConfigured(f"Add your Jooble API key to .env ({names}). Register per country, e.g. "
                                      "https://in.jooble.org/api/about for India.")

    def _budget(self) -> int:
        try:
            return max(1, min(20, int(self.config.get("requests_per_run") or 3)))
        except (TypeError, ValueError):
            return 3

    def search(self, query: SearchQuery) -> list[RawListing]:
        self.check_configuration()
        budget = self._budget()
        keywords = (query.keywords or ["service delivery manager"])[:budget]
        plan: list[tuple[str, str, str]] = []
        for country in self._countries():
            if not jooble_key(country):
                continue
            host, default_location = JOOBLE_COUNTRIES[country]
            locations = [default_location]
            if country == "in" and self.config.get("locations"):
                locations = [l.strip() for l in str(self.config["locations"]).split(",") if l.strip()]
            for location in locations:
                for keyword in keywords:
                    plan.append((country, keyword, location))
        plan = plan[:budget]

        results: list[RawListing] = []
        spent = 0
        exhausted = []
        for country, keyword, location in plan:
            used = self.usage.used(country) if self.usage else 0
            if used >= JOOBLE_LIFETIME_LIMIT - JOOBLE_SAFETY_MARGIN:
                exhausted.append(country)
                continue
            host, country_name = JOOBLE_COUNTRIES[country]
            data, cached = self.http.post_json(
                f"https://{host}/api/{jooble_key(country)}",
                {"keywords": keyword, "location": location, "page": 1,
                 "ResultOnPage": min(query.max_results, 50), "companysearch": "false"},
                cache_ttl=12 * 3600, max_retries=0, safe_path="/api/<key>",
            )
            if not cached and self.usage:
                self.usage.record(country)
                spent += 1
            jobs = data.get("jobs") if isinstance(data, dict) else None
            if jobs is None:
                raise SourceFailure("Jooble response did not contain a 'jobs' array.")
            for job in jobs:
                try:
                    results.append(RawListing(
                        source_name=self.name,
                        source_url=job["link"],
                        title=sanitize_text(job["title"], 300),
                        company=sanitize_text(job.get("company") or "", 200),
                        location=sanitize_text(job.get("location") or "", 200),
                        country=country_name,
                        description=html_to_text(job.get("snippet")),
                        posting_date=None,  # 'updated' is not a posting date
                        employment_type=job.get("type") or None,
                        retrieved_at=self.now(),
                    ))
                except (KeyError, TypeError):
                    continue
        if exhausted and not results:
            from discovery.base import SourceQuotaExhausted

            raise SourceQuotaExhausted(
                f"Jooble free quota nearly used for: {', '.join(sorted(set(exhausted)))} "
                f"({JOOBLE_LIFETIME_LIMIT} requests per key). Request a new key or disable Jooble.")
        # Jooble results need a company name to be traceable; drop anonymous ones.
        return keyword_prefilter([r for r in results if r.company.strip()], query)
