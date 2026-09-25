"""Evidence-grounded match analysis.

Hard eligibility is decided before this step by deterministic rules and is never
changed here. This module assembles evidence, computes a documented internal
relevance score, and (optionally) asks Claude for a structured explanation that
is validated against the evidence before use.

Relevance score (0-100, *internal relevance, not a hiring probability*)::

    100 * ( w_skill    * skill_coverage          # confirmed skills / (confirmed + missing listing requirements)
          + w_title    * title_alignment         # best token overlap of job title with target titles/headline
          + w_evidence * evidence_completeness   # share of evidence checks satisfied (see below)
          + w_recency  * recency )               # 1.0 if posted <=7 days ago, linear to 0 at 60 days, 0 if unknown

Evidence completeness checks: verified posting date, determined location,
stated work arrangement, resume evidence retrieved, >=2 skills with listing
evidence. Confidence is ``high`` (>=0.8), ``medium`` (>=0.5) or ``low``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from config.settings import parse_iso, utcnow
from database.models import CandidateProfile, EvidenceSpan, LocationDecision, MatchResult, SkillMatch
from rag.embeddings import tokenize
from rag.retrieval import ResumeIndex, RetrievedEvidence, retrieve_job_evidence
from screening.skill_filter import missing_skills, required_skills_in_listing
from screening.text_utils import detect_injection
from screening.validation import LLMMatchOutput, ValidationOutcome, validate_llm_output

DEFAULT_WEIGHTS = {"skill_coverage": 0.5, "title_alignment": 0.2, "evidence_completeness": 0.2, "recency": 0.1}
MAX_REPAIR_ATTEMPTS = 1


@dataclass
class MatchInputs:
    job_key: str
    title: str
    company: str
    location: str
    description: str
    source_url: str
    posting_date: str | None
    skill_matches: list[SkillMatch]
    location_decision: LocationDecision
    candidate_skills: list[str]


@dataclass
class EvidenceBundle:
    resume: list[RetrievedEvidence] = field(default_factory=list)
    job: list[RetrievedEvidence] = field(default_factory=list)
    required: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    injection_flags: list[str] = field(default_factory=list)


def gather_evidence(inputs: MatchInputs, resume_index: ResumeIndex | None, backend: str = "hashing") -> EvidenceBundle:
    confirmed = [m.skill for m in inputs.skill_matches]
    query = f"{inputs.title}. " + ", ".join(confirmed)
    bundle = EvidenceBundle()
    if resume_index is not None:
        bundle.resume = resume_index.retrieve(query, confirmed, k=4)
    bundle.job = retrieve_job_evidence(inputs.description, inputs.job_key, confirmed, query, backend=backend, k=5)
    bundle.required = required_skills_in_listing(inputs.description)
    bundle.missing = missing_skills(bundle.required, inputs.candidate_skills)
    bundle.injection_flags = detect_injection(inputs.description)
    return bundle


def title_alignment(title: str, targets: list[str]) -> float:
    job_tokens = set(tokenize(title))
    best = 0.0
    for target in targets:
        t = set(tokenize(target))
        if t:
            best = max(best, len(t & job_tokens) / len(t))
    return round(best, 3)


def recency_score(posting_date: str | None) -> float:
    posted = parse_iso(posting_date)
    if not posted:
        return 0.0
    days = max(0.0, (utcnow() - posted).total_seconds() / 86400)
    if days <= 7:
        return 1.0
    return round(max(0.0, 1 - (days - 7) / 53), 3)


def compute_relevance(inputs: MatchInputs, bundle: EvidenceBundle, targets: list[str],
                      weights: dict[str, float] | None = None) -> tuple[float, dict[str, float], float, list[str]]:
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    confirmed = len(inputs.skill_matches)
    coverage = confirmed / max(1, confirmed + len(bundle.missing))
    checks = {
        "posting date verified": parse_iso(inputs.posting_date) is not None,
        "location determined": inputs.location_decision.region != "unknown" or bool(
            inputs.location_decision.remote_eligibility),
        "work arrangement stated": inputs.location_decision.work_arrangement != "unknown",
        "resume evidence retrieved": bool(bundle.resume),
        "two or more skills evidenced in listing": confirmed >= 2,
    }
    completeness = sum(checks.values()) / len(checks)
    parts = {
        "skill_coverage": round(coverage, 3),
        "title_alignment": title_alignment(inputs.title, targets),
        "evidence_completeness": round(completeness, 3),
        "recency": recency_score(inputs.posting_date),
    }
    total_w = sum(weights.values()) or 1.0
    score = round(100 * sum(weights[k] * parts[k] for k in parts) / total_w, 1)
    basis = [f"{'✔' if ok else '✘'} {name}" for name, ok in checks.items()]
    return score, parts, completeness, basis


def _confidence(completeness: float) -> str:
    return "high" if completeness >= 0.8 else "medium" if completeness >= 0.5 else "low"


def deterministic_explanation(inputs: MatchInputs, bundle: EvidenceBundle) -> str:
    skills = ", ".join(m.skill for m in inputs.skill_matches)
    sections = sorted({r.chunk.section for r in bundle.resume if r.chunk.section})
    loc = inputs.location_decision
    parts = [f"The listing mentions {len(inputs.skill_matches)} of the candidate's confirmed skills ({skills})."]
    if sections:
        parts.append(f"Related resume evidence was found in: {', '.join(sections)}.")
    parts.append(f"Location: {inputs.location or 'not stated'} ({loc.work_arrangement}); {loc.reason}")
    if bundle.missing:
        parts.append(f"Listing requirements not confirmed in the profile: {', '.join(bundle.missing[:8])}.")
    return " ".join(parts)


def _visa_text(loc: LocationDecision) -> str | None:
    items = []
    if loc.remote_eligibility:
        items.append(f"Remote from India: \"{loc.remote_eligibility}\"")
    if loc.relocation_evidence:
        items.append(f"Relocation: \"{loc.relocation_evidence}\"")
    if loc.visa_sponsorship_evidence:
        items.append(f"Visa sponsorship: \"{loc.visa_sponsorship_evidence}\"")
    return "; ".join(items) or None


def run_llm_explanation(llm_call: Callable[[list[dict]], str], user_prompt: str, *, allowed_skills: list[str],
                        resume_passages: list[str], job_passages: list[str]) -> tuple[ValidationOutcome, int]:
    """Call the model, validate, and allow one bounded repair attempt."""
    messages = [{"role": "user", "content": user_prompt}]
    attempts = 0
    outcome = ValidationOutcome(ok=False, errors=["not attempted"])
    from agent.prompts import REPAIR_PROMPT

    while attempts <= MAX_REPAIR_ATTEMPTS:
        attempts += 1
        raw = llm_call(messages)
        outcome = validate_llm_output(raw, allowed_skills=allowed_skills, resume_passages=resume_passages,
                                      job_passages=job_passages)
        if outcome.ok:
            break
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": REPAIR_PROMPT.format(errors="\n".join(f"- {e}" for e in outcome.errors))},
        ]
    return outcome, attempts


def build_match_result(inputs: MatchInputs, bundle: EvidenceBundle, *, profile: CandidateProfile,
                       target_titles: list[str], weights: dict[str, float] | None,
                       llm_output: LLMMatchOutput | None, llm_notes: list[str]) -> MatchResult:
    score, parts, completeness, basis = compute_relevance(inputs, bundle, target_titles + (
        [profile.headline] if profile.headline else []), weights)
    candidate_evidence = [
        EvidenceSpan(text=r.chunk.text[:500], source="resume", section=r.chunk.section, page=r.chunk.page,
                     start=r.chunk.start, end=r.chunk.end)
        for r in bundle.resume
    ]
    job_evidence = [m.evidence for m in inputs.skill_matches]
    seen = {e.text for e in job_evidence}
    for r in bundle.job:
        if r.chunk.text not in seen:
            job_evidence.append(EvidenceSpan(text=r.chunk.text[:500], source="job_listing", start=r.chunk.start,
                                             end=r.chunk.end, url=inputs.source_url))
    notes = list(llm_notes)
    if bundle.injection_flags:
        notes.append("Listing contains instruction-like text (treated as data): " + "; ".join(bundle.injection_flags))

    explanation = deterministic_explanation(inputs, bundle)
    missing = list(bundle.missing)
    if llm_output is not None:
        explanation = llm_output.summary.strip()
        for item in llm_output.relevant_experience:
            candidate_evidence.insert(0, EvidenceSpan(text=f"{item.claim} — “{item.resume_quote}”", source="resume"))
        for item in llm_output.job_requirements:
            job_evidence.append(EvidenceSpan(text=f"{item.requirement} — “{item.listing_quote}”",
                                             source="job_listing", url=inputs.source_url))
        missing = list(dict.fromkeys(missing + llm_output.unverified_requirements))[:15]
        notes.extend(f"Model concern: {c}" for c in llm_output.concerns)

    loc = inputs.location_decision
    return MatchResult(
        match_status=loc.status,
        explanation=explanation,
        confirmed_skills=[m.skill for m in inputs.skill_matches],
        missing_or_unverified_skills=missing,
        candidate_evidence=candidate_evidence,
        job_evidence=job_evidence,
        location_and_arrangement=f"{inputs.location or 'Location not stated'} · {loc.work_arrangement}"
                                 + (f" · {loc.country}" if loc.country else ""),
        visa_relocation_findings=_visa_text(loc),
        confidence=_confidence(completeness),
        confidence_basis=basis,
        relevance_score=score,
        relevance_breakdown=parts,
        source_url=inputs.source_url,
        llm_used=llm_output is not None,
        validation_notes=notes,
    )
