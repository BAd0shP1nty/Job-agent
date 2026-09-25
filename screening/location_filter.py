"""Location, work-arrangement and overseas-eligibility screening (deterministic).

Rules
-----
India: office, hybrid and remote roles are accepted. Jobs outside Bangalore are
kept unless the user enabled the *Bangalore-only* filter.

Outside India: accepted only with explicit evidence of (a) remote work open to
someone in India, (b) relocation assistance, or (c) visa sponsorship. Missing or
ambiguous evidence -> ``requires_verification`` (kept out of the queue).
Overseas roles outside the target markets (India + EMEA) are only accepted when
remote work from India is verified.
"""
from __future__ import annotations

import re

from database.models import LocationDecision
from screening.visa_filter import Finding, find_relocation, find_remote_from_india, find_visa_sponsorship

INDIA_CITIES = [
    "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "navi mumbai", "thane", "delhi", "new delhi", "delhi ncr",
    "ncr", "gurgaon", "gurugram", "noida", "greater noida", "faridabad", "ghaziabad", "chennai", "kolkata",
    "ahmedabad", "gandhinagar", "kochi", "cochin", "trivandrum", "thiruvananthapuram", "coimbatore", "mysore",
    "mysuru", "mangalore", "jaipur", "indore", "bhopal", "nagpur", "lucknow", "chandigarh", "mohali", "vadodara",
    "surat", "visakhapatnam", "vizag", "bhubaneswar", "goa", "karnataka", "maharashtra", "telangana", "tamil nadu",
    "haryana", "uttar pradesh", "kerala", "west bengal", "gujarat",
]
BANGALORE_ALIASES = {"bangalore", "bengaluru"}

EMEA_COUNTRIES: dict[str, list[str]] = {
    "United Kingdom": ["united kingdom", "uk", "england", "scotland", "wales", "northern ireland", "london",
                       "manchester", "edinburgh", "glasgow", "birmingham", "leeds", "bristol", "cambridge", "oxford",
                       "milton keynes", "reading", "belfast", "great britain", "gb"],
    "Ireland": ["ireland", "dublin", "cork", "galway", "limerick"],
    "Germany": ["germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne", "köln",
                "stuttgart", "düsseldorf", "dusseldorf", "leipzig", "dresden"],
    "France": ["france", "paris", "lyon", "toulouse", "marseille", "lille", "nantes", "bordeaux", "sophia antipolis"],
    "Netherlands": ["netherlands", "holland", "amsterdam", "rotterdam", "the hague", "utrecht", "eindhoven"],
    "Belgium": ["belgium", "brussels", "antwerp", "ghent"],
    "Luxembourg": ["luxembourg"],
    "Spain": ["spain", "madrid", "barcelona", "valencia", "malaga", "málaga"],
    "Portugal": ["portugal", "lisbon", "porto"],
    "Italy": ["italy", "milan", "rome", "turin"],
    "Switzerland": ["switzerland", "zurich", "zürich", "geneva", "basel", "lausanne"],
    "Austria": ["austria", "vienna"],
    "Sweden": ["sweden", "stockholm", "gothenburg", "malmö"],
    "Norway": ["norway", "oslo"],
    "Denmark": ["denmark", "copenhagen"],
    "Finland": ["finland", "helsinki"],
    "Poland": ["poland", "warsaw", "krakow", "kraków", "wroclaw", "wrocław", "gdansk"],
    "Czech Republic": ["czech republic", "czechia", "prague", "brno"],
    "Romania": ["romania", "bucharest", "cluj"],
    "Hungary": ["hungary", "budapest"],
    "Greece": ["greece", "athens"],
    "Estonia": ["estonia", "tallinn"],
    "Lithuania": ["lithuania", "vilnius"],
    "Latvia": ["latvia", "riga"],
    "Cyprus": ["cyprus", "limassol", "nicosia"],
    "Malta": ["malta"],
    "United Arab Emirates": ["united arab emirates", "uae", "dubai", "abu dhabi", "sharjah"],
    "Saudi Arabia": ["saudi arabia", "ksa", "riyadh", "jeddah", "dammam"],
    "Qatar": ["qatar", "doha"],
    "Bahrain": ["bahrain", "manama"],
    "Kuwait": ["kuwait"],
    "Oman": ["oman", "muscat"],
    "Israel": ["israel", "tel aviv", "haifa"],
    "Turkey": ["turkey", "türkiye", "istanbul", "ankara"],
    "Egypt": ["egypt", "cairo"],
    "South Africa": ["south africa", "johannesburg", "cape town", "pretoria", "durban"],
    "Kenya": ["kenya", "nairobi"],
    "Nigeria": ["nigeria", "lagos", "abuja"],
    "Morocco": ["morocco", "casablanca", "rabat"],
}
EMEA_REGION_WORDS = ["emea", "europe", "european union", "middle east", "africa", "eu"]
OTHER_COUNTRIES = {
    "United States": ["united states", "usa", "u.s.", "us", "new york", "san francisco", "seattle", "austin",
                      "boston", "chicago", "los angeles", "california", "texas"],
    "Canada": ["canada", "toronto", "vancouver", "montreal"],
    "Singapore": ["singapore"],
    "Australia": ["australia", "sydney", "melbourne"],
    "Japan": ["japan", "tokyo"],
}


_UPPERCASE_ONLY = {"us", "uk", "eu", "gb"}


def _has(term: str, text: str) -> bool:
    if term in _UPPERCASE_ONLY:  # avoid matching the English word "us"
        return re.search(rf"(?<![\w.]){re.escape(term.upper())}(?![\w.])", text) is not None
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def detect_country(location: str, country_hint: str | None = None) -> tuple[str | None, str]:
    """Return (country, region) where region is india | emea | other | unknown."""
    for candidate in (country_hint or "", location or ""):
        if not candidate.strip():
            continue
        if _has("india", candidate) or any(_has(c, candidate) for c in INDIA_CITIES):
            return "India", "india"
        for country, terms in EMEA_COUNTRIES.items():
            if any(_has(t, candidate) for t in terms):
                return country, "emea"
        for country, terms in OTHER_COUNTRIES.items():
            if any(_has(t, candidate) for t in terms):
                return country, "other"
        if any(_has(w, candidate) for w in EMEA_REGION_WORDS):
            return None, "emea"
    return None, "unknown"


_REMOTE = re.compile(r"\b(fully remote|100% remote|remote[- ]first|remote[- ]only|work from home|wfh|remote)\b", re.I)
_HYBRID = re.compile(r"\b(hybrid)\b", re.I)
_ONSITE = re.compile(r"\b(on[- ]?site|in[- ]office|office[- ]based|work from office|wfo)\b", re.I)


def detect_work_arrangement(hint: str | None, title: str, location: str, description: str) -> str:
    """Conservative: hybrid wins over remote when both appear; unknown if nothing is stated."""
    if hint:
        h = hint.lower()
        if "hybrid" in h:
            return "hybrid"
        if "remote" in h or "telecommute" in h:
            return "remote"
        if "onsite" in h or "on-site" in h or "office" in h:
            return "office"
    head = f"{title} \n {location}"
    for text in (head, description[:4000]):
        if _HYBRID.search(text):
            return "hybrid"
        if _REMOTE.search(text):
            return "remote"
        if _ONSITE.search(text):
            return "office"
    return "unknown"


def screen_location(
    *,
    title: str,
    location: str,
    country_hint: str | None,
    description: str,
    work_arrangement_hint: str | None,
    remote_location_field: str | None,
    source_url: str,
    bangalore_only: bool = False,
    remote_only: bool = False,
) -> LocationDecision:
    country, region = detect_country(location, country_hint)
    arrangement = detect_work_arrangement(work_arrangement_hint, title, location, description)

    if remote_only and arrangement != "remote":
        return LocationDecision(status="ineligible", country=country, region=region, work_arrangement=arrangement,
                                reason=f"Remote-only filter is on and the role is '{arrangement}'.")

    if region == "india":
        if bangalore_only:
            text = f"{location} {country_hint or ''}".lower()
            in_blr = any(a in text for a in BANGALORE_ALIASES)
            if not (in_blr or arrangement == "remote"):
                return LocationDecision(status="ineligible", country=country, region=region,
                                        work_arrangement=arrangement,
                                        reason="Bangalore-only filter is on and the role is not in Bangalore or remote.")
        return LocationDecision(status="eligible", country="India", region=region, work_arrangement=arrangement,
                                reason="India-based role.")

    # Outside India (or unknown): explicit evidence required.
    remote = find_remote_from_india(description, remote_location_field) if arrangement == "remote" or \
        remote_location_field else Finding("absent")
    if arrangement != "remote" and remote.status == "absent":
        # A listing might still say "work from anywhere" without a structured arrangement.
        remote = find_remote_from_india(description, None)
        if remote.status == "verified" and arrangement == "hybrid":
            remote = Finding("ambiguous", remote.evidence, "hybrid role with a remote statement")
    relocation = find_relocation(description)
    visa = find_visa_sponsorship(description)

    decision = LocationDecision(
        status="ineligible", country=country, region=region, work_arrangement=arrangement, reason="",
        remote_eligibility=remote.quote if remote.status == "verified" else None,
        relocation_evidence=relocation.quote if relocation.status == "verified" else None,
        visa_sponsorship_evidence=visa.quote if visa.status == "verified" else None,
        evidence_source_url=source_url,
    )
    verified = [name for name, f in (("remote work from India", remote), ("relocation", relocation),
                                     ("visa sponsorship", visa)) if f.status == "verified"]
    ambiguous = [name for name, f in (("remote eligibility", remote), ("relocation", relocation),
                                      ("visa sponsorship", visa)) if f.status == "ambiguous"]

    if region == "unknown" and not verified:
        decision.status = "requires_verification"
        decision.reason = "Job location could not be determined from the listing."
        return decision

    if region == "other" and "remote work from India" not in verified:
        decision.status = "ineligible"
        decision.reason = f"Outside target markets (India/EMEA): {country or location}."
        decision.relocation_evidence = decision.visa_sponsorship_evidence = None
        return decision

    if verified:
        decision.status = "eligible"
        decision.reason = "Overseas role with verified " + ", ".join(verified) + "."
        return decision
    if ambiguous:
        decision.status = "requires_verification"
        decision.reason = "Overseas role; ambiguous evidence for " + ", ".join(ambiguous) + " - not inferred."
        return decision
    decision.reason = "Overseas role without explicit remote-from-India, relocation or visa sponsorship evidence."
    decision.evidence_source_url = None
    return decision
