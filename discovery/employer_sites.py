"""Employer career-site connectors.

Many employers publish their openings through applicant-tracking systems that
expose documented, public job-board APIs (Greenhouse, Lever, Ashby). Employers
whose career pages embed schema.org ``JobPosting`` JSON-LD can be added by URL;
those pages are fetched only when robots.txt permits.

Each connector needs the user to configure which employers to watch.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from database.models import RawListing
from discovery.base import ConnectorInfo, SearchQuery, SourceAccessDenied, SourceAdapter, SourceFailure, \
    SourceNotConfigured, keyword_prefilter
from screening.text_utils import html_to_text, sanitize_text


def _entries(config: dict, key: str) -> list[tuple[str, str | None]]:
    """Config entries are 'id' or 'id|Display Name' (comma or newline separated)."""
    raw = config.get(key) or ""
    items = raw if isinstance(raw, list) else str(raw).replace("\n", ",").split(",")
    out = []
    for item in items:
        item = str(item).strip()
        if not item:
            continue
        ident, _, display = item.partition("|")
        out.append((ident.strip(), display.strip() or None))
    return out


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 1e12 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


class GreenhouseConnector(SourceAdapter):
    info = ConnectorInfo(
        name="greenhouse",
        display_name="Employer boards via Greenhouse API",
        kind="employer_api",
        description="Official public Job Board API used by many employers' career sites.",
        access_notes="Public API, no key. Configure employer board tokens (the part after "
                     "boards.greenhouse.io/<token>).",
        config_fields={"boards": "Board tokens, comma-separated, optionally 'token|Company Name'"},
        terms_url="https://developers.greenhouse.io/job-board.html",
    )

    def check_configuration(self) -> None:
        if not _entries(self.config, "boards"):
            raise SourceNotConfigured("Add at least one Greenhouse board token in Sources & Connectors.")

    def search(self, query: SearchQuery) -> list[RawListing]:
        self.check_configuration()
        results: list[RawListing] = []
        errors = []
        for token, display in _entries(self.config, "boards"):
            try:
                company = display
                if not company:
                    meta = self.http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}", cache_ttl=86400)
                    company = (meta or {}).get("name") or token
                data = self.http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                                          {"content": "true"})
            except SourceFailure as exc:
                errors.append(f"{token}: {exc}")
                continue
            for job in (data or {}).get("jobs", []):
                try:
                    results.append(RawListing(
                        source_name=self.name,
                        source_url=job["absolute_url"],
                        title=sanitize_text(job["title"], 300),
                        company=company,
                        location=sanitize_text((job.get("location") or {}).get("name") or "", 200),
                        description=html_to_text(job.get("content")),
                        posting_date=_iso(job.get("first_published")),  # updated_at is not a posting date
                        requisition_id=str(job["requisition_id"]) if job.get("requisition_id") else None,
                        retrieved_at=self.now(),
                    ))
                except (KeyError, TypeError):
                    continue
        if errors and not results:
            raise SourceFailure("; ".join(errors))
        return keyword_prefilter(results, query)


class LeverConnector(SourceAdapter):
    info = ConnectorInfo(
        name="lever",
        display_name="Employer boards via Lever Postings API",
        kind="employer_api",
        description="Official public Postings API used by many employers' career sites.",
        access_notes="Public API, no key. Configure company slugs (jobs.lever.co/<slug>). Set region=eu for "
                     "jobs.eu.lever.co boards.",
        config_fields={"companies": "Company slugs, comma-separated, optionally 'slug|Company Name'",
                       "region": "global or eu"},
        terms_url="https://github.com/lever/postings-api",
    )

    def check_configuration(self) -> None:
        if not _entries(self.config, "companies"):
            raise SourceNotConfigured("Add at least one Lever company slug in Sources & Connectors.")

    def search(self, query: SearchQuery) -> list[RawListing]:
        self.check_configuration()
        host = "api.eu.lever.co" if str(self.config.get("region", "")).lower() == "eu" else "api.lever.co"
        results: list[RawListing] = []
        errors = []
        for slug, display in _entries(self.config, "companies"):
            try:
                data = self.http.get_json(f"https://{host}/v0/postings/{slug}", {"mode": "json"})
            except SourceFailure as exc:
                errors.append(f"{slug}: {exc}")
                continue
            if not isinstance(data, list):
                errors.append(f"{slug}: unexpected response shape")
                continue
            for job in data:
                try:
                    cats = job.get("categories") or {}
                    lists = "\n".join(f"{l.get('text', '')}\n{html_to_text(l.get('content'))}" for l in job.get("lists") or [])
                    description = "\n".join(filter(None, [
                        job.get("descriptionPlain") or html_to_text(job.get("description")), lists,
                        job.get("additionalPlain") or html_to_text(job.get("additional"))]))
                    results.append(RawListing(
                        source_name=self.name,
                        source_url=job["hostedUrl"],
                        title=sanitize_text(job["text"], 300),
                        company=display or slug,
                        location=sanitize_text(cats.get("location") or "", 200),
                        country=job.get("country"),
                        description=sanitize_text(description),
                        posting_date=_iso(job.get("createdAt")),
                        work_arrangement_hint=job.get("workplaceType"),
                        employment_type=cats.get("commitment"),
                        retrieved_at=self.now(),
                    ))
                except (KeyError, TypeError):
                    continue
        if errors and not results:
            raise SourceFailure("; ".join(errors))
        return keyword_prefilter(results, query)


class AshbyConnector(SourceAdapter):
    info = ConnectorInfo(
        name="ashby",
        display_name="Employer boards via Ashby Job Posting API",
        kind="employer_api",
        description="Official public job posting API used by many employers' career sites.",
        access_notes="Public API, no key. Configure job board names (jobs.ashbyhq.com/<name>).",
        config_fields={"boards": "Board names, comma-separated, optionally 'name|Company Name'"},
        terms_url="https://developers.ashbyhq.com/docs/public-job-posting-api",
    )

    def check_configuration(self) -> None:
        if not _entries(self.config, "boards"):
            raise SourceNotConfigured("Add at least one Ashby job board name in Sources & Connectors.")

    def search(self, query: SearchQuery) -> list[RawListing]:
        self.check_configuration()
        results: list[RawListing] = []
        errors = []
        for board, display in _entries(self.config, "boards"):
            try:
                data = self.http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}",
                                          {"includeCompensation": "false"})
            except SourceFailure as exc:
                errors.append(f"{board}: {exc}")
                continue
            for job in (data or {}).get("jobs", []):
                try:
                    if job.get("isListed") is False:
                        continue
                    address = ((job.get("address") or {}).get("postalAddress") or {})
                    hint = job.get("workplaceType") or ("remote" if job.get("isRemote") else None)
                    results.append(RawListing(
                        source_name=self.name,
                        source_url=job["jobUrl"],
                        title=sanitize_text(job["title"], 300),
                        company=display or board,
                        location=sanitize_text(job.get("location") or "", 200),
                        country=address.get("addressCountry"),
                        description=job.get("descriptionPlain") and sanitize_text(job["descriptionPlain"])
                        or html_to_text(job.get("descriptionHtml")),
                        posting_date=_iso(job.get("publishedAt")),
                        work_arrangement_hint=hint,
                        employment_type=job.get("employmentType"),
                        retrieved_at=self.now(),
                    ))
                except (KeyError, TypeError):
                    continue
        if errors and not results:
            raise SourceFailure("; ".join(errors))
        return keyword_prefilter(results, query)


def parse_jobposting_jsonld(html_text: str, page_url: str) -> list[dict]:
    """Extract schema.org JobPosting objects from a page."""
    soup = BeautifulSoup(html_text, "html.parser")
    postings: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                types = node.get("@type")
                types = types if isinstance(types, list) else [types]
                if "JobPosting" in types:
                    postings.append(node)
                if "@graph" in node:
                    stack.extend(node["@graph"] if isinstance(node["@graph"], list) else [node["@graph"]])
            elif isinstance(node, list):
                stack.extend(node)
    return postings


def jobposting_to_listing(node: dict, page_url: str, source_name: str, retrieved_at: str) -> RawListing | None:
    title = node.get("title")
    org = node.get("hiringOrganization")
    company = org.get("name") if isinstance(org, dict) else org if isinstance(org, str) else None
    if not title or not company:
        return None
    locations = node.get("jobLocation") or []
    locations = locations if isinstance(locations, list) else [locations]
    loc_names, country = [], None
    for loc in locations:
        addr = (loc or {}).get("address") if isinstance(loc, dict) else None
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion")]
            c = addr.get("addressCountry")
            c = c.get("name") if isinstance(c, dict) else c
            country = country or c
            loc_names.append(", ".join(p for p in parts + [c] if p))
    reqs = node.get("applicantLocationRequirements") or []
    reqs = reqs if isinstance(reqs, list) else [reqs]
    req_names = [r.get("name") if isinstance(r, dict) else str(r) for r in reqs]
    remote = str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE"
    ident = node.get("identifier")
    ident = ident.get("value") if isinstance(ident, dict) else ident
    return RawListing(
        source_name=source_name,
        source_url=node.get("url") or page_url,
        title=sanitize_text(str(title), 300),
        company=sanitize_text(str(company), 200),
        location="; ".join(loc_names) or ("Remote" if remote else ""),
        country=country,
        description=html_to_text(node.get("description")),
        posting_date=_iso(node.get("datePosted")),
        work_arrangement_hint="remote" if remote else None,
        employment_type=", ".join(node["employmentType"]) if isinstance(node.get("employmentType"), list)
        else node.get("employmentType"),
        remote_location_field=", ".join(n for n in req_names if n) or None,
        requisition_id=str(ident) if ident else None,
        retrieved_at=retrieved_at,
    )


class CareerPageConnector(SourceAdapter):
    info = ConnectorInfo(
        name="career_pages",
        display_name="Employer career pages (schema.org JobPosting)",
        kind="public_page",
        description="Reads structured JobPosting data embedded in employer career pages you list.",
        access_notes="Fetches only URLs you add, only when robots.txt allows, rate-limited and cached. Pages "
                     "without JobPosting JSON-LD are skipped (no free-text scraping).",
        config_fields={"urls": "Career page or job page URLs, one per line"},
    )

    def check_configuration(self) -> None:
        if not _entries(self.config, "urls"):
            raise SourceNotConfigured("Add at least one career page URL in Sources & Connectors.")

    def search(self, query: SearchQuery) -> list[RawListing]:
        self.check_configuration()
        results: list[RawListing] = []
        errors, denied = [], []
        for url, _ in _entries(self.config, "urls"):
            try:
                page = self.http.get_page(url)
            except SourceAccessDenied as exc:
                denied.append(f"{url}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - one bad page must not stop the others
                errors.append(f"{url}: {exc}")
                continue
            for node in parse_jobposting_jsonld(page, url):
                listing = jobposting_to_listing(node, url, self.name, self.now())
                if listing:
                    results.append(listing)
        if denied and not results and not errors:
            raise SourceAccessDenied("; ".join(denied)[:500])
        if (errors or denied) and not results:
            raise SourceFailure("; ".join(errors + denied)[:500])
        return keyword_prefilter(results, query)
