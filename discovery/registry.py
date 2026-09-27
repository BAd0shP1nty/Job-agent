"""Source registry: which connectors exist, their persisted status, and isolated execution."""
from __future__ import annotations

from dataclasses import dataclass

from database.connection import Database
from database.models import RawListing
from database.repository import HttpCacheRepository, SourceRegistryRepository, UsageTracker
from discovery.api_connectors import (
    AdzunaConnector,
    ArbeitnowConnector,
    HimalayasConnector,
    JoobleConnector,
    RemotiveConnector,
)
from discovery.base import HttpClient, SearchQuery, SourceAdapter, SourceError
from discovery.employer_sites import AshbyConnector, CareerPageConnector, GreenhouseConnector, LeverConnector
from discovery.public_job_boards import IndeedConnector, LinkedInConnector, NaukriConnector, TestFixtureConnector

CONNECTOR_CLASSES: list[type[SourceAdapter]] = [
    RemotiveConnector,
    ArbeitnowConnector,
    HimalayasConnector,
    AdzunaConnector,
    JoobleConnector,
    GreenhouseConnector,
    LeverConnector,
    AshbyConnector,
    CareerPageConnector,
    LinkedInConnector,
    NaukriConnector,
    IndeedConnector,
    TestFixtureConnector,
]


@dataclass
class SourceRunResult:
    source_name: str
    listings: list[RawListing]
    error: str | None = None
    status: str = "working"


class SourceRegistry:
    def __init__(self, db: Database, http: HttpClient | None = None,
                 extra_connectors: list[type[SourceAdapter]] | None = None):
        self.db = db
        self.repo = SourceRegistryRepository(db)
        self.http = http or HttpClient(cache=HttpCacheRepository(db))
        self.classes = {cls.info.name: cls for cls in CONNECTOR_CLASSES + list(extra_connectors or [])}
        for cls in self.classes.values():
            self.repo.ensure(cls.info.name, cls.info.enabled_by_default)
        # Record static access limitations so the GUI never shows these as working.
        for cls in self.classes.values():
            if cls.info.kind == "restricted":
                entry = self.repo.get(cls.info.name)
                if entry and entry["status"] != "requires_authorized_access":
                    self.repo.set_status(cls.info.name, "requires_authorized_access", cls.info.access_notes)

    def adapter(self, name: str) -> SourceAdapter:
        entry = self.repo.get(name) or {"config": {}}
        return self.classes[name](self.http, entry.get("config") or {}, usage=UsageTracker(self.db, name))

    def entries(self) -> list[dict]:
        rows = {r["source_name"]: r for r in self.repo.list()}
        out = []
        for name, cls in self.classes.items():
            row = rows.get(name, {})
            out.append({**row, "info": cls.info})
        return out

    def enabled_names(self) -> list[str]:
        return [r["source_name"] for r in self.repo.list() if r["enabled"] and r["source_name"] in self.classes]

    def run_source(self, name: str, query: SearchQuery) -> SourceRunResult:
        """Run one connector in isolation. Failures are recorded, never raised."""
        adapter = self.adapter(name)
        try:
            adapter.check_configuration()
            listings = adapter.search(query)
        except SourceError as exc:
            self.repo.record_failure(name, exc.status, str(exc))
            return SourceRunResult(name, [], str(exc), exc.status)
        except Exception as exc:  # noqa: BLE001 - a buggy connector must not stop the run
            self.repo.record_failure(name, "failing", f"{exc.__class__.__name__}: {exc}")
            return SourceRunResult(name, [], f"{exc.__class__.__name__}: {exc}", "failing")
        self.repo.record_success(name)
        return SourceRunResult(name, listings)

    def test_connection(self, name: str) -> SourceRunResult:
        return self.run_source(name, SearchQuery(keywords=["service"], skills=[], max_results=5))
