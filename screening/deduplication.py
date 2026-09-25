"""URL canonicalization, text normalization and job fingerprints.

Keys used for duplicate detection:

* ``canonical_url_hash`` - hash of the canonical listing URL (tracking params removed).
* ``fingerprint`` - strict key: normalized company | title | city | country | requisition id.
* ``loose_key`` - the same without the requisition id; used to catch the same job
  posted on another portal. When two listings share a loose key but carry
  *different* explicit requisition ids they are treated as different jobs, so the
  fingerprint never suppresses genuinely different openings from one employer.

A fuzzy title match (same company + location, very similar title) is never
auto-merged; it is routed to a ``duplicate_review`` state instead.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id", "utm_name",
    "gclid", "fbclid", "msclkid", "dclid", "yclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
    "ref", "refid", "referer", "referrer", "trk", "trkinfo", "trackingid", "tracking_id", "src", "source",
    "lipi", "originalsubdomain", "currentjobid", "eborigin", "from", "campaign", "cid", "sid", "rx_campaign",
    "rx_source", "rx_medium", "gh_src", "lever-source", "lever-origin", "iis", "iisn", "ccuid", "feedid",
    "share", "shared_from", "sharesource", "_ga", "_gl", "icid", "position", "pagenum", "refnum",
}

_COMPANY_SUFFIXES = re.compile(
    r"\b(private limited|pvt\.? ltd\.?|pvt|ltd\.?|limited|inc\.?|incorporated|llc|llp|plc|gmbh|ag|bv|b\.v\.|"
    r"s\.a\.|sa|sas|corp\.?|corporation|co\.?|company|group|holdings)\b"
)
_TITLE_ABBREVIATIONS = [
    (r"\bsr\b\.?", "senior"),
    (r"\bjr\b\.?", "junior"),
    (r"\bmgr\b", "manager"),
    (r"\bengg?\b", "engineer"),
    (r"\bdev\b", "developer"),
    (r"\bassoc\b", "associate"),
    (r"\bvp\b", "vice president"),
    (r"\bmgmt\b", "management"),
    (r"&", " and "),
]
_TITLE_NOISE = re.compile(
    r"\b(remote|hybrid|onsite|on site|on-site|wfh|work from home|full time|full-time|permanent|contract|urgent|"
    r"hiring|immediate joiner[s]?)\b"
)
_CITY_ALIASES = {
    "bengaluru": "bangalore", "gurugram": "gurgaon", "bombay": "mumbai", "madras": "chennai",
    "new delhi": "delhi", "delhi ncr": "delhi", "ncr": "delhi", "noida": "noida", "calcutta": "kolkata",
    "münchen": "munich", "munchen": "munich", "köln": "cologne", "koln": "cologne",
}


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _basic_normalize(text: str | None) -> str:
    text = _strip_accents((text or "").lower())
    text = re.sub(r"[^\w\s&]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonicalize_url(url: str) -> str:
    """Normalize a listing URL so tracking variants map to one canonical form."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":443") or host.endswith(":80"):
        host = host.rsplit(":", 1)[0]
    # Regional LinkedIn subdomains (in.linkedin.com) point at the same listing.
    if host.endswith(".linkedin.com"):
        host = "linkedin.com"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    query.sort()
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def url_hash(url: str) -> str:
    return sha256(canonicalize_url(url))


def normalize_company(company: str | None) -> str:
    text = _basic_normalize(company)
    text = _COMPANY_SUFFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(title: str | None) -> str:
    text = _strip_accents((title or "").lower())
    text = re.sub(r"\([^)]*\)", " ", text)  # "(Remote)", "(m/f/d)"
    text = re.sub(r"\b[mwfd]\s*/\s*[mwfd](\s*/\s*[mwfdx])?\b", " ", text)
    for pattern, repl in _TITLE_ABBREVIATIONS:
        text = re.sub(pattern, repl, text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = _TITLE_NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_location(location: str | None, country: str | None = None) -> str:
    text = _basic_normalize(location)
    for alias, canonical in _CITY_ALIASES.items():
        text = re.sub(rf"\b{re.escape(alias)}\b", canonical, text)
    text = re.sub(r"\b(remote|hybrid|onsite|on site|office|india|in|area|metropolitan|region)\b", " ", text)
    tokens = text.split()
    city = tokens[0] if tokens else ""
    return f"{city}|{_basic_normalize(country)}"


def loose_key(title: str, company: str, location: str | None, country: str | None = None) -> str:
    return sha256("|".join([normalize_company(company), normalize_title(title), normalize_location(location, country)]))


def job_fingerprint(
    title: str, company: str, location: str | None, country: str | None = None, requisition_id: str | None = None
) -> str:
    req = _basic_normalize(requisition_id)
    return sha256(loose_key(title, company, location, country) + "|" + req)


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


FUZZY_TITLE_THRESHOLD = 0.85
