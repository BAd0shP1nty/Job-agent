"""Strict validation of model output against the schema *and* the supplied evidence."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError


class ExperienceClaim(BaseModel):
    claim: str = Field(min_length=1, max_length=400)
    resume_quote: str = Field(min_length=3, max_length=600)


class RequirementClaim(BaseModel):
    requirement: str = Field(min_length=1, max_length=400)
    listing_quote: str = Field(min_length=3, max_length=600)


class LLMMatchOutput(BaseModel):
    summary: str = Field(min_length=10, max_length=1200)
    relevant_experience: list[ExperienceClaim] = Field(default_factory=list, max_length=10)
    job_requirements: list[RequirementClaim] = Field(default_factory=list, max_length=12)
    confirmed_skills: list[str] = Field(default_factory=list, max_length=40)
    unverified_requirements: list[str] = Field(default_factory=list, max_length=20)
    concerns: list[str] = Field(default_factory=list, max_length=10)


_HIRING_CLAIMS = re.compile(
    r"(\d{1,3}\s*%\s*(chance|probability|likel)|probability of (being )?(hired|interview)|guarantee[sd]? (an? )?"
    r"(interview|offer|hire)|application (has been|was) submitted|you will be hired)",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _is_quoted(quote: str, passages: list[str]) -> bool:
    q = _norm(quote).strip(" .\"'…")
    if len(q) < 3:
        return False
    return any(q in _norm(p) for p in passages)


class ValidationOutcome(BaseModel):
    ok: bool
    output: LLMMatchOutput | None = None
    errors: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)


def validate_llm_output(raw_text: str, *, allowed_skills: list[str], resume_passages: list[str],
                        job_passages: list[str]) -> ValidationOutcome:
    """Parse and ground-check model output.

    Unsupported items are dropped (and reported). The outcome fails when the JSON
    is malformed, violates the schema, makes hiring-probability claims, or when
    most of its evidence claims could not be verified against the passages.
    """
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        return ValidationOutcome(ok=False, errors=[f"Output is not valid JSON: {exc}"])
    try:
        parsed = LLMMatchOutput.model_validate(data)
    except ValidationError as exc:
        return ValidationOutcome(ok=False, errors=[f"Schema violation: {e['loc']} {e['msg']}" for e in exc.errors()][:8])

    errors: list[str] = []
    dropped: list[str] = []
    if _HIRING_CLAIMS.search(parsed.summary):
        errors.append("Summary makes a hiring-probability or application-submission claim.")

    allowed = {s.casefold(): s for s in allowed_skills}
    skills = []
    for s in parsed.confirmed_skills:
        if s.casefold() in allowed:
            skills.append(allowed[s.casefold()])
        else:
            dropped.append(f"skill not in confirmed list: {s}")

    experience = []
    for item in parsed.relevant_experience:
        if resume_passages and _is_quoted(item.resume_quote, resume_passages):
            experience.append(item)
        else:
            dropped.append(f"unsupported resume quote: {item.resume_quote[:80]}")

    requirements = []
    for item in parsed.job_requirements:
        if _is_quoted(item.listing_quote, job_passages):
            requirements.append(item)
        else:
            dropped.append(f"unsupported listing quote: {item.listing_quote[:80]}")

    claims_total = len(parsed.relevant_experience) + len(parsed.job_requirements)
    claims_kept = len(experience) + len(requirements)
    if claims_total and claims_kept / claims_total < 0.5:
        errors.append(f"Only {claims_kept} of {claims_total} evidence quotes were found verbatim in the passages.")

    cleaned = parsed.model_copy(update={"confirmed_skills": skills, "relevant_experience": experience,
                                        "job_requirements": requirements})
    return ValidationOutcome(ok=not errors, output=cleaned, errors=errors, dropped=dropped)
