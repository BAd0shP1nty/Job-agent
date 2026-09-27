"""Connector tests with a mocked HTTP session returning each provider's documented response shape.

These verify parsing, rate limiting, retries, robots.txt handling and error mapping.
They do not prove live connectivity (see README: connector status).
"""
import json

import pytest
import requests

from database.repository import HttpCacheRepository
from discovery.api_connectors import AdzunaConnector, ArbeitnowConnector, HimalayasConnector, RemotiveConnector
from discovery.base import HttpClient, SearchQuery, SourceAccessDenied, SourceFailure, SourceNotConfigured
from discovery.employer_sites import AshbyConnector, CareerPageConnector, GreenhouseConnector, LeverConnector
from discovery.public_job_boards import LinkedInConnector
from discovery.base import SourceAccessRestricted

QUERY = SearchQuery(keywords=["service delivery"], skills=["ITIL", "SRE"], max_results=50)


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, routes):
        self.routes = routes  # list of (substring, response or list of responses)
        self.headers = {}
        self.calls = []

    def post(self, url, json=None, timeout=None):
        return self.get(url, params=json, timeout=timeout)

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for key, resp in self.routes:
            if key in url:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return FakeResponse(404)


def client(routes, db=None):
    sleeps = []
    http = HttpClient(cache=HttpCacheRepository(db) if db else None, session=FakeSession(routes),
                      sleep=sleeps.append, min_interval=0.5, backoff_base=1.0)
    http.sleeps = sleeps
    return http


def test_remotive_parsing():
    payload = {"jobs": [{"id": 1, "url": "https://remotive.com/remote-jobs/x/1", "title": "SRE Lead",
                         "company_name": "Acme", "candidate_required_location": "Worldwide",
                         "publication_date": "2026-09-20T10:00:00", "job_type": "full_time",
                         "description": "<p>Lead our <b>SRE</b> team.</p>"}]}
    listings = RemotiveConnector(client([("remotive.com", FakeResponse(200, payload))])).search(QUERY)
    assert len(listings) == 1
    l = listings[0]
    assert l.remote_location_field == "Worldwide" and l.work_arrangement_hint == "remote"
    assert l.description == "Lead our SRE team." and l.posting_date.startswith("2026-09-20")


def test_arbeitnow_parsing():
    payload = {"data": [{"slug": "a", "company_name": "Beispiel GmbH", "title": "ITIL Service Manager",
                         "description": "<p>ITIL processes</p>", "remote": False, "url": "https://www.arbeitnow.com/jobs/a",
                         "tags": [], "job_types": ["full time"], "location": "Berlin", "created_at": 1790000000}]}
    session_routes = [("arbeitnow.com", [FakeResponse(200, payload), FakeResponse(200, {"data": []})])]
    http = client(session_routes)
    listings = ArbeitnowConnector(http).search(QUERY)
    assert listings[0].company == "Beispiel GmbH" and listings[0].posting_date is not None
    assert listings[0].work_arrangement_hint is None


def test_himalayas_empty_restrictions_are_unknown_not_worldwide():
    payload = {"jobs": [{"title": "SRE Manager", "companyName": "Acme", "description": "SRE work",
                         "locationRestrictions": [], "pubDate": 1790000000, "applicationLink": "https://x.example/1",
                         "guid": "g1"},
                        {"title": "ITIL Lead", "companyName": "Beta", "description": "ITIL",
                         "locationRestrictions": ["India", "Singapore"], "pubDate": 1790000000,
                         "applicationLink": "https://x.example/2", "guid": "g2"}]}
    listings = HimalayasConnector(client([("himalayas.app", FakeResponse(200, payload))])).search(QUERY)
    by_title = {l.title: l for l in listings}
    assert by_title["SRE Manager"].remote_location_field is None
    assert by_title["ITIL Lead"].remote_location_field == "India, Singapore"


def test_adzuna_requires_credentials():
    with pytest.raises(SourceNotConfigured):
        AdzunaConnector(client([])).check_configuration()


def test_greenhouse_parsing_uses_first_published_not_updated_at():
    jobs = {"jobs": [{"id": 1, "title": "Service Delivery Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                      "location": {"name": "Bengaluru, India"}, "updated_at": "2026-09-24T00:00:00Z",
                      "content": "&lt;p&gt;Run ITIL service delivery.&lt;/p&gt;"}]}
    http = client([("/boards/acme/jobs", FakeResponse(200, jobs)), ("/boards/acme", FakeResponse(200, {"name": "Acme Corp"}))])
    listings = GreenhouseConnector(http, {"boards": "acme"}).search(QUERY)
    assert listings[0].company == "Acme Corp"
    assert listings[0].posting_date is None
    assert listings[0].description == "Run ITIL service delivery."


def test_greenhouse_requires_board_config():
    with pytest.raises(SourceNotConfigured):
        GreenhouseConnector(client([]), {}).check_configuration()


def test_lever_parsing():
    payload = [{"id": "x", "text": "SRE Lead", "categories": {"location": "Dublin", "commitment": "Full-time"},
                "descriptionPlain": "Lead SRE.", "lists": [{"text": "Benefits", "content": "<li>Relocation assistance is provided.</li>"}],
                "additionalPlain": "", "hostedUrl": "https://jobs.lever.co/acme/x", "createdAt": 1790000000000,
                "workplaceType": "onsite", "country": "IE"}]
    listings = LeverConnector(client([("api.eu.lever.co", FakeResponse(200, payload))]),
                              {"companies": "acme|Acme Ltd", "region": "eu"}).search(QUERY)
    assert listings[0].company == "Acme Ltd"
    assert "Relocation assistance is provided." in listings[0].description


def test_ashby_parsing_skips_unlisted():
    payload = {"jobs": [{"title": "SRE Manager", "location": "Remote", "isRemote": True, "descriptionPlain": "SRE",
                         "publishedAt": "2026-09-01T00:00:00Z", "jobUrl": "https://jobs.ashbyhq.com/acme/1"},
                        {"title": "Hidden SRE", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/acme/2",
                         "descriptionPlain": "SRE"}]}
    listings = AshbyConnector(client([("ashbyhq.com", FakeResponse(200, payload))]), {"boards": "acme"}).search(QUERY)
    assert [l.title for l in listings] == ["SRE Manager"]
    assert listings[0].work_arrangement_hint == "remote"


JSONLD_PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"JobPosting","title":"ITIL Process Lead",
"hiringOrganization":{"@type":"Organization","name":"Example Org"},"datePosted":"2026-09-10",
"description":"<p>Own ITIL processes. We offer visa sponsorship.</p>","jobLocationType":"TELECOMMUTE",
"applicantLocationRequirements":[{"@type":"Country","name":"India"}],"identifier":{"value":"REQ-9"},
"url":"https://careers.example.com/jobs/9"}]}
</script></head><body></body></html>"""


def test_career_page_jsonld():
    http = client([("robots.txt", FakeResponse(200, text="User-agent: *\nAllow: /")),
                   ("careers.example.com", FakeResponse(200, text=JSONLD_PAGE))])
    listings = CareerPageConnector(http, {"urls": "https://careers.example.com/jobs"}).search(QUERY)
    l = listings[0]
    assert (l.title, l.company, l.requisition_id, l.remote_location_field) == \
        ("ITIL Process Lead", "Example Org", "REQ-9", "India")
    assert l.work_arrangement_hint == "remote"


def test_robots_disallow_blocks_page_fetch():
    http = client([("robots.txt", FakeResponse(200, text="User-agent: *\nDisallow: /")),
                   ("careers.example.com", FakeResponse(200, text=JSONLD_PAGE))])
    with pytest.raises(SourceAccessDenied, match="robots.txt"):
        CareerPageConnector(http, {"urls": "https://careers.example.com/jobs"}).search(QUERY)
    assert not any("careers.example.com/jobs" in c[0] for c in http.session.calls)


def test_http_403_maps_to_blocked():
    http = client([("remotive.com", FakeResponse(403))])
    with pytest.raises(SourceAccessDenied):
        RemotiveConnector(http).search(QUERY)


def test_proxy_refusal_maps_to_blocked():
    http = client([("remotive.com", requests.exceptions.ProxyError("403 CONNECT"))])
    with pytest.raises(SourceAccessDenied, match="Network policy"):
        RemotiveConnector(http).search(QUERY)


def test_retry_with_exponential_backoff_then_success():
    ok = FakeResponse(200, {"jobs": []})
    http = client([("remotive.com", [FakeResponse(503), FakeResponse(429), ok])])
    RemotiveConnector(http).search(SearchQuery(keywords=["x"], skills=[]))
    backoffs = [s for s in http.sleeps if s >= 1.0]
    assert backoffs[:2] == [1.0, 2.0]


def test_retries_are_bounded():
    http = client([("remotive.com", [FakeResponse(503)])])
    with pytest.raises(SourceFailure, match="503"):
        http.get_json("https://remotive.com/api/remote-jobs")
    assert len(http.session.calls) == 4  # 1 + 3 retries


def test_rate_limit_spacing_between_calls():
    http = client([("remotive.com", FakeResponse(200, {"jobs": []}))])
    http.get_json("https://remotive.com/a", cache_ttl=0)
    http.get_json("https://remotive.com/b", cache_ttl=0)
    assert any(0 < s <= 0.5 for s in http.sleeps)


def test_malformed_json_is_reported():
    http = client([("remotive.com", FakeResponse(200, text="<html>oops</html>"))])
    with pytest.raises(SourceFailure, match="malformed JSON"):
        http.get_json("https://remotive.com/api")


def test_responses_are_cached(db):
    http = client([("remotive.com", FakeResponse(200, {"jobs": []}))], db=db)
    http.get_json("https://remotive.com/api", {"q": 1})
    http.get_json("https://remotive.com/api", {"q": 1})
    assert len(http.session.calls) == 1


def test_malformed_listing_items_are_skipped():
    payload = {"jobs": [{"title": "no url"}, {"url": "https://remotive.com/1", "title": "SRE", "company_name": "A",
                                              "description": "SRE"}]}
    listings = RemotiveConnector(client([("remotive.com", FakeResponse(200, payload))])).search(QUERY)
    assert [l.title for l in listings] == ["SRE"]


def test_restricted_portal_refuses():
    with pytest.raises(SourceAccessRestricted):
        LinkedInConnector(client([])).check_configuration()
