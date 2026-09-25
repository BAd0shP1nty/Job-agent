"""Deterministic evidence finders for overseas eligibility.

For a job outside India to pass, the listing (or a structured field supplied by
the source) must *explicitly* state at least one of:

* remote work is available to someone working from India,
* relocation assistance / a relocation option,
* visa sponsorship / an employer-sponsored work visa.

Each finder classifies the evidence as ``verified``, ``negative``, ``ambiguous``
or ``absent``. Hedged wording ("may", "case-by-case") and contradictory
statements are ``ambiguous`` and are never upgraded to verified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from screening.text_utils import Sentence, split_sentences

FindingStatus = Literal["verified", "negative", "ambiguous", "absent"]

_HEDGE = re.compile(
    r"\b(may|might|possibly|possible|potential(ly)?|could|case[- ]by[- ]case|depending on|where applicable|"
    r"subject to|in some cases|limited|not guaranteed|to be discussed|negotiable|consider(ed)?)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(no|not|unable|cannot|can't|can not|won't|will not|does not|do not|don't|without|excluding|except|"
    r"neither|nor|isn't|aren't|is not|are not)\b",
    re.IGNORECASE,
)


@dataclass
class Finding:
    status: FindingStatus
    evidence: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def quote(self) -> str | None:
        return " … ".join(self.evidence) if self.evidence else None


def _negated_near(text: str, match: re.Match, before: int = 60, after: int = 0) -> bool:
    """True when a negation word appears close to (mostly before) the matched phrase."""
    window = text[max(0, match.start() - before): match.end() + after]
    return _NEGATION.search(window) is not None


def _classify(sentences: list[Sentence], positive: re.Pattern, negative: re.Pattern | None) -> Finding:
    verified, hedged, negatives = [], [], []
    for s in sentences:
        text = s.text
        if negative is not None and negative.search(text):
            negatives.append(text)
            continue
        m = positive.search(text)
        if m:
            if _negated_near(text, m):
                negatives.append(text)
            elif _HEDGE.search(text):
                hedged.append(text)
            else:
                verified.append(text)
    if verified and negatives:
        return Finding("ambiguous", verified[:1] + negatives[:1], "contradictory statements")
    if verified:
        return Finding("verified", verified[:2])
    if hedged:
        return Finding("ambiguous", hedged[:2], "hedged wording")
    if negatives:
        return Finding("negative", negatives[:2])
    return Finding("absent")


# --- visa sponsorship --------------------------------------------------------
_VISA_POS = re.compile(
    r"(visa sponsorship|sponsor(ship)? (of |for )?(your |a |the )?(work )?(visa|permit)|sponsor (you|candidates|"
    r"international|visas?)|we (will |can |do )?sponsor|provide (a )?(work )?visa|employer[- ]sponsored (work )?visa|"
    r"work permit (support|sponsorship|assistance)|(blue card|skilled worker visa|critical skills (employment )?permit|"
    r"kennismigrant|highly skilled migrant|h-?1b|tier 2) (sponsorship|support|visa|application)|visa support|visas? (is |are |will be )?(provided|offered|sponsored)|"
    r"(support|assist|help) (you )?(with|in) (your |the )?(visa|work permit))",
    re.IGNORECASE,
)
_VISA_NEG = re.compile(
    r"(no (visa )?sponsorship|(unable|not able) to (offer |provide )?(visa )?sponsor|cannot sponsor|can't sponsor|"
    r"(do|does|will) not (offer |provide )?(visa )?sponsor|sponsorship (is )?not (available|offered|provided|possible)|"
    r"without (the need for |requiring )?(visa )?sponsorship|must (already )?(have|hold|possess) (the |a valid )?"
    r"(right|authori[sz]ation|permit|eligibility) to work|existing (right|authori[sz]ation) to work)",
    re.IGNORECASE,
)

# --- relocation --------------------------------------------------------------
_RELOC_POS = re.compile(
    r"(relocation (assistance|support|package|allowance|bonus|budget|benefits?|is (provided|offered|available)|"
    r"provided|offered|available|costs?|help)|(offer|provide|support|cover|pay for|assist with|help with)s? "
    r"(your |full |the )?relocation|help (you )?relocate|relocate (you|candidates)|visa and relocation|"
    r"relocation and visa)",
    re.IGNORECASE,
)
_RELOC_NEG = re.compile(
    r"(no relocation|relocation (is )?not (available|provided|offered|supported|possible)|(unable|not able) to "
    r"(offer|provide|support) relocation|(do|does|will) not (offer|provide|support|cover) relocation|"
    r"without relocation|local candidates only|must (already )?(be )?(live|living|reside|residing|based|located) "
    r"(in|within|near))",
    re.IGNORECASE,
)

# --- remote from India -------------------------------------------------------
_REMOTE_WORD = re.compile(r"\b(remote(ly)?|work from (home|anywhere)|distributed|anywhere)\b", re.IGNORECASE)
_INDIA_ELIGIBLE = re.compile(
    r"\b(india|indian|apac|asia[- ]pacific|asia)\b", re.IGNORECASE
)
_WORLDWIDE = re.compile(
    r"\b(anywhere in the world|work from anywhere|worldwide|world-wide|globally remote|remote[- ]global|"
    r"any country|any location|from any country|location[- ]independent|fully distributed across the globe)\b",
    re.IGNORECASE,
)
_RESIDENCY_RESTRICTION = re.compile(
    r"(must (be )?(based|located|reside|residing|live|living) in|only (open to|accepting|considering) "
    r"(candidates|applicants)|(candidates|applicants) (must|need to) (be )?(based|located|reside)|"
    r"(right|authori[sz]ed|eligible|eligibility) to work in|\b(us|usa|uk|eu|europe|emea|germany|france|"
    r"netherlands|ireland|canada)[- ]only\b|(us|uk|eu)[- ]based (candidates|applicants) only|within (the )?(us|uk|eu|"
    r"european union|europe|emea)\b)",
    re.IGNORECASE,
)


_SPONSOR_MENTION = re.compile(r"\b(sponsor(s|ed|ing|ship)?|work permits?|work visas?)\b", re.IGNORECASE)


def find_visa_sponsorship(text: str) -> Finding:
    sentences = split_sentences(text)
    finding = _classify(sentences, _VISA_POS, _VISA_NEG)
    if finding.status == "absent":
        # Hedged mentions that don't match the explicit patterns are still ambiguous, never verified.
        hedged = [s.text for s in sentences if _SPONSOR_MENTION.search(s.text) and _HEDGE.search(s.text)]
        if hedged:
            return Finding("ambiguous", hedged[:2], "hedged wording")
    return finding


def find_relocation(text: str) -> Finding:
    return _classify(split_sentences(text), _RELOC_POS, _RELOC_NEG)


def find_remote_from_india(text: str, structured_location: str | None = None) -> Finding:
    """Remote eligibility for a candidate working from India.

    "Remote" alone is not enough; India (or APAC), or an explicit worldwide /
    work-from-anywhere statement must be present, and no residency restriction
    that excludes India may contradict it.
    """
    sentences = split_sentences(text)
    restrictions = [s.text for s in sentences if _RESIDENCY_RESTRICTION.search(s.text)
                    and not _INDIA_ELIGIBLE.search(s.text)]

    if structured_location:
        loc = structured_location.strip()
        if _INDIA_ELIGIBLE.search(loc) and not _NEGATION.search(loc):
            if restrictions:
                return Finding("ambiguous", [f"Candidate location: {loc}", restrictions[0]], "contradictory statements")
            return Finding("verified", [f"Candidate location (structured field): {loc}"])
        if _WORLDWIDE.search(loc) or loc.lower() in {"anywhere", "worldwide", "global"}:
            if restrictions:
                return Finding("ambiguous", [f"Candidate location: {loc}", restrictions[0]], "contradictory statements")
            return Finding("verified", [f"Candidate location (structured field): {loc}"])

    verified, hedged, negatives = [], [], []
    for s in sentences:
        t = s.text
        india = _INDIA_ELIGIBLE.search(t)
        worldwide = _WORLDWIDE.search(t)
        if not ((india and _REMOTE_WORD.search(t)) or worldwide):
            continue
        if _negated_near(t, india or worldwide, before=40, after=10):
            negatives.append(t)
        elif _HEDGE.search(t):
            hedged.append(t)
        else:
            verified.append(t)
    if verified and (negatives or restrictions):
        return Finding("ambiguous", verified[:1] + (negatives or restrictions)[:1], "contradictory statements")
    if verified:
        return Finding("verified", verified[:2])
    if hedged:
        return Finding("ambiguous", hedged[:2], "hedged wording")
    if negatives or restrictions:
        return Finding("negative", (negatives or restrictions)[:2])
    return Finding("absent")
