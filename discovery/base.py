"""Common connector interface and a polite HTTP client.

Connectors must only use official APIs, authorized integrations or public pages
whose robots.txt allows access. They never bypass authentication, CAPTCHA or
rate limits: HTTP 401/403 and robots.txt disallows surface as
``SourceAccessDenied`` and the connector is marked accordingly in the registry.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.robotparser
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

import requests

from config.logging_config import get_logger
from config.settings import get_settings, utcnow_iso
from database.models import RawListing

log = get_logger("discovery")


class SourceError(RuntimeError):
    status = "error"


class SourceNotConfigured(SourceError):
    """Credentials or configuration (e.g. employer board tokens) are missing."""

    status = "needs_configuration"


class SourceAccessRestricted(SourceError):
    """The portal offers no permitted programmatic access without a partner agreement."""

    status = "requires_authorized_access"


class SourceAccessDenied(SourceError):
    """HTTP 401/403, robots.txt disallow, or a network policy blocked the request."""

    status = "blocked"


class SourceFailure(SourceError):
    status = "failing"


class SourceQuotaExhausted(SourceError):
    """A free API quota is (nearly) used up; the connector stops before exceeding it."""

    status = "quota_exhausted"


@dataclass
class SearchQuery:
    keywords: list[str]
    skills: list[str]
    locations: list[str] = field(default_factory=list)
    remote_only: bool = False
    max_results: int = 50
    date_posted_days: int | None = 30


@dataclass
class ConnectorInfo:
    name: str
    display_name: str
    kind: str                       # "official_api" | "employer_api" | "public_page" | "restricted" | "test_fixture"
    description: str
    access_notes: str
    enabled_by_default: bool = False
    requires_credentials: bool = False
    config_fields: dict[str, str] = field(default_factory=dict)   # key -> help text
    secret_fields: dict[str, str] = field(default_factory=dict)   # env var name -> label (entered in the GUI)
    terms_url: str | None = None


class SourceAdapter(ABC):
    info: ConnectorInfo

    def __init__(self, http: "HttpClient", config: dict[str, Any] | None = None, usage=None):
        self.http = http
        self.config = config or {}
        self.usage = usage  # optional UsageTracker for APIs with request quotas

    @property
    def name(self) -> str:
        return self.info.name

    def check_configuration(self) -> None:
        """Raise SourceNotConfigured / SourceAccessRestricted when the connector cannot run."""

    @abstractmethod
    def search(self, query: SearchQuery) -> list[RawListing]:
        ...

    # helpers ------------------------------------------------------------------
    @staticmethod
    def now() -> str:
        return utcnow_iso()


def keyword_prefilter(listings: list[RawListing], query: SearchQuery) -> list[RawListing]:
    """Cheap relevance pre-filter for boards that return all openings.

    Keeps listings whose title or description mentions a target keyword or skill.
    The mandatory skill rule is still enforced later by the screening stage.
    """
    terms = [t.lower() for t in query.keywords + query.skills if t and len(t) > 1]
    if not terms:
        return listings[: query.max_results]
    kept = []
    for listing in listings:
        blob = f"{listing.title}\n{listing.description}".lower()
        if any(term in blob for term in terms):
            kept.append(listing)
    return kept[: query.max_results]


class HttpClient:
    """requests wrapper with per-host rate limiting, retries with exponential backoff,
    response caching and robots.txt checks for HTML pages."""

    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, cache=None, min_interval: float = 1.5, max_retries: int = 3, backoff_base: float = 1.0,
                 session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep):
        settings = get_settings()
        self.cache = cache
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": settings.http_user_agent, "Accept": "application/json, text/html"})
        self.timeout = settings.http_timeout
        self.sleep = sleep
        self._last_call: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # rate limiting -------------------------------------------------------------
    def _throttle(self, host: str) -> None:
        last = self._last_call.get(host)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_call[host] = time.monotonic()

    # robots.txt ----------------------------------------------------------------
    def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                resp = self.session.get(f"{base}/robots.txt", timeout=self.timeout)
                if resp.status_code in (401, 403):
                    self._robots[base] = None  # treat as disallow-all
                elif resp.status_code >= 400:
                    parser.parse([])  # no robots.txt -> allowed
                    self._robots[base] = parser
                else:
                    parser.parse(resp.text.splitlines())
                    self._robots[base] = parser
            except requests.exceptions.ProxyError as exc:
                raise SourceAccessDenied(f"Network policy/proxy refused the connection to {parts.netloc}.") from exc
            except requests.RequestException:
                self._robots[base] = None  # robots.txt unreachable -> do not fetch
        parser = self._robots[base]
        if parser is None:
            return False
        return parser.can_fetch(self.session.headers["User-Agent"], url)

    # requests --------------------------------------------------------------------
    def _request(self, url: str, params: dict | None, *, method: str = "GET", json_body: Any = None,
                 max_retries: int | None = None, safe_path: str | None = None) -> requests.Response:
        """Send a request with bounded retries.

        ``safe_path`` replaces the URL path in error messages - used when the path
        contains a secret such as an API key.
        """
        host = urlsplit(url).netloc
        path = safe_path if safe_path is not None else urlsplit(url).path
        retries = self.max_retries if max_retries is None else max_retries
        attempt = 0
        while True:
            self._throttle(host)
            try:
                if method == "POST":
                    resp = self.session.post(url, json=json_body, timeout=self.timeout)
                else:
                    resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.exceptions.ProxyError as exc:
                raise SourceAccessDenied(f"Network policy/proxy refused the connection to {host}.") from exc
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= retries:
                    raise SourceFailure(f"Network error contacting {host}: {exc.__class__.__name__}") from exc
                self.sleep(self.backoff_base * (2 ** attempt))
                attempt += 1
                continue
            if resp.status_code in (401, 403):
                raise SourceAccessDenied(f"{host} denied access (HTTP {resp.status_code}).")
            if resp.status_code in self.RETRY_STATUSES and attempt < retries:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff_base * (2 ** attempt)
                self.sleep(min(delay, 60))
                attempt += 1
                continue
            if resp.status_code == 404:
                raise SourceFailure(f"{host} returned 404 for {path} (check the board/company id).")
            if resp.status_code >= 400:
                raise SourceFailure(f"{host} returned HTTP {resp.status_code}.")
            return resp

    def _cache_key(self, url: str, params: dict | None) -> str:
        return hashlib.sha256((url + "?" + urlencode(sorted((params or {}).items()))).encode()).hexdigest()

    def get_json(self, url: str, params: dict | None = None, cache_ttl: int = 3600) -> Any:
        key = self._cache_key(url, params)
        if self.cache is not None and cache_ttl > 0:
            cached = self.cache.get(key, cache_ttl)
            if cached is not None:
                return json.loads(cached)
        resp = self._request(url, params)
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceFailure(f"{urlsplit(url).netloc} returned malformed JSON.") from exc
        if self.cache is not None and cache_ttl > 0:
            self.cache.put(key, json.dumps(data))
        return data

    def post_json(self, url: str, payload: dict, cache_ttl: int = 3600, max_retries: int | None = None,
                  safe_path: str | None = None) -> tuple[Any, bool]:
        """POST a JSON body. Returns (data, served_from_cache)."""
        key = self._cache_key(url, {"__post__": json.dumps(payload, sort_keys=True)})
        if self.cache is not None and cache_ttl > 0:
            cached = self.cache.get(key, cache_ttl)
            if cached is not None:
                return json.loads(cached), True
        resp = self._request(url, None, method="POST", json_body=payload, max_retries=max_retries,
                             safe_path=safe_path)
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceFailure(f"{urlsplit(url).netloc} returned malformed JSON.") from exc
        if self.cache is not None and cache_ttl > 0:
            self.cache.put(key, json.dumps(data))
        return data, False

    def get_page(self, url: str, cache_ttl: int = 3600) -> str:
        if not self.allowed_by_robots(url):
            raise SourceAccessDenied(f"robots.txt does not permit fetching {url}")
        key = self._cache_key(url, None)
        if self.cache is not None and cache_ttl > 0:
            cached = self.cache.get(key, cache_ttl)
            if cached is not None:
                return cached
        text = self._request(url, None).text
        if self.cache is not None and cache_ttl > 0:
            self.cache.put(key, text)
        return text
