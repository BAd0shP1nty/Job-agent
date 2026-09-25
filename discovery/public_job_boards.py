"""Major job portals that do not offer permitted open programmatic access, plus the
clearly labelled test-fixture source.

LinkedIn, Naukri and Indeed are registered so their status is visible in the GUI,
but they refuse to run: their terms prohibit automated scraping and their job
search APIs are available only to approved partners. They will not be scraped,
and no substitute listings are ever produced for them.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from config.settings import PROJECT_ROOT, utcnow
from database.models import RawListing
from discovery.base import ConnectorInfo, SearchQuery, SourceAccessRestricted, SourceAdapter


class _RestrictedPortal(SourceAdapter):
    def check_configuration(self) -> None:
        raise SourceAccessRestricted(self.info.access_notes)

    def search(self, query: SearchQuery) -> list[RawListing]:
        self.check_configuration()
        return []


class LinkedInConnector(_RestrictedPortal):
    info = ConnectorInfo(
        name="linkedin",
        display_name="LinkedIn Jobs",
        kind="restricted",
        description="Not connected.",
        access_notes="LinkedIn has no public job-search API; job data is available only to approved LinkedIn "
                     "partners (Talent Solutions / Apply Connect). Its User Agreement prohibits scraping, so this "
                     "connector stays disabled until an authorized partner integration is provided.",
        terms_url="https://www.linkedin.com/legal/user-agreement",
    )


class NaukriConnector(_RestrictedPortal):
    info = ConnectorInfo(
        name="naukri",
        display_name="Naukri.com",
        kind="restricted",
        description="Not connected.",
        access_notes="Naukri offers no public job-search API for job seekers; programmatic access requires a "
                     "commercial/recruiter agreement with Info Edge. Automated scraping is not permitted, so this "
                     "connector stays disabled until authorized access is available.",
        terms_url="https://www.naukri.com/termsconditions",
    )


class IndeedConnector(_RestrictedPortal):
    info = ConnectorInfo(
        name="indeed",
        display_name="Indeed",
        kind="restricted",
        description="Not connected.",
        access_notes="Indeed's publisher Job Search API is closed to new integrations and its terms prohibit "
                     "scraping. Requires an approved Indeed partner integration.",
        terms_url="https://www.indeed.com/legal",
    )


FIXTURE_FILE = PROJECT_ROOT / "tests" / "fixtures" / "mock_jobs.json"


def load_fixture_listings(path: Path | None = None) -> list[RawListing]:
    """Mock listings for repeatable tests and demos. These are NOT real vacancies."""
    data = json.loads((path or FIXTURE_FILE).read_text(encoding="utf-8"))
    now = utcnow()
    listings = []
    for item in data["listings"]:
        item = {k: v for k, v in item.items() if not k.startswith("_")}
        days = item.pop("posted_days_ago", None)
        item.setdefault("source_name", "test_fixtures")
        item["posting_date"] = (now - timedelta(days=days)).isoformat(timespec="seconds") if days is not None else None
        item["retrieved_at"] = now.isoformat(timespec="seconds")
        item["is_test_fixture"] = True
        listings.append(RawListing(**item))
    return listings


class TestFixtureConnector(SourceAdapter):
    __test__ = False  # not a pytest test class

    info = ConnectorInfo(
        name="test_fixtures",
        display_name="TEST FIXTURES (mock data - not real vacancies)",
        kind="test_fixture",
        description="Fictional listings on example.com used to demonstrate and test the full lifecycle.",
        access_notes="Local file tests/fixtures/mock_jobs.json. Every job it produces is badged TEST FIXTURE.",
    )

    def search(self, query: SearchQuery) -> list[RawListing]:
        return load_fixture_listings(self.config.get("path") and Path(self.config["path"]))
