"""Shared data models (pydantic) used across discovery, screening, RAG and persistence."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

JobStatus = Literal["pending", "duplicate_review", "selected", "applied"]
EligibilityStatus = Literal["eligible", "ineligible", "requires_verification"]
Confidence = Literal["high", "medium", "low"]

SELECTED_STATUS_OPTIONS = ["Selected", "Applied"]


class EvidenceSpan(BaseModel):
    """A quoted piece of evidence with its provenance."""

    text: str
    source: Literal["resume", "job_listing", "structured_field"]
    section: str | None = None
    page: int | None = None
    start: int | None = None
    end: int | None = None
    url: str | None = None


class RawListing(BaseModel):
    """A listing as returned by a source connector, before screening.

    All text fields are untrusted external content.
    """

    source_name: str
    source_url: str
    title: str
    company: str
    location: str = ""
    country: str | None = None
    description: str = ""
    posting_date: str | None = None
    work_arrangement_hint: str | None = None      # e.g. "remote", "hybrid", "onsite" from structured data
    employment_type: str | None = None
    seniority: str | None = None
    remote_location_field: str | None = None      # structured "candidate location" restriction, if any
    requisition_id: str | None = None             # employer requisition number when explicitly provided
    retrieved_at: str
    is_test_fixture: bool = False


class ExperienceEntry(BaseModel):
    employer: str = ""
    title: str = ""
    dates: str = ""
    location: str = ""
    highlights: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    name: str | None = None
    headline: str | None = None
    current_location: str | None = None
    total_experience: str | None = None
    skills: list[str] = Field(default_factory=list)                 # confirmed, from the resume
    skill_evidence: dict[str, str] = Field(default_factory=dict)    # skill -> resume snippet
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    target_titles: list[str] = Field(default_factory=list)
    location_preferences: list[str] = Field(default_factory=list)
    work_arrangements: list[str] = Field(default_factory=lambda: ["remote", "hybrid", "office"])
    target_markets: list[str] = Field(default_factory=lambda: ["India", "EMEA"])
    resume_sections: list[dict] = Field(default_factory=list)       # [{name, start, end, page}] offsets only


class SkillMatch(BaseModel):
    skill: str
    matched_term: str
    evidence: EvidenceSpan


class LocationDecision(BaseModel):
    status: EligibilityStatus
    country: str | None = None
    region: Literal["india", "emea", "other", "unknown"] = "unknown"
    work_arrangement: str = "unknown"
    reason: str
    remote_eligibility: str | None = None
    relocation_evidence: str | None = None
    visa_sponsorship_evidence: str | None = None
    evidence_source_url: str | None = None


class ScreeningResult(BaseModel):
    status: EligibilityStatus
    reasons: list[str] = Field(default_factory=list)
    skill_matches: list[SkillMatch] = Field(default_factory=list)
    location: LocationDecision | None = None


class MatchResult(BaseModel):
    match_status: EligibilityStatus
    explanation: str
    confirmed_skills: list[str]
    missing_or_unverified_skills: list[str] = Field(default_factory=list)
    candidate_evidence: list[EvidenceSpan] = Field(default_factory=list)
    job_evidence: list[EvidenceSpan] = Field(default_factory=list)
    location_and_arrangement: str = ""
    visa_relocation_findings: str | None = None
    confidence: Confidence = "low"
    confidence_basis: list[str] = Field(default_factory=list)
    relevance_score: float | None = None
    relevance_breakdown: dict[str, float] = Field(default_factory=dict)
    source_url: str
    llm_used: bool = False
    validation_notes: list[str] = Field(default_factory=list)
