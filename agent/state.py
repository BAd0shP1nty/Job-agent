"""LangGraph state. Everything in the state is JSON-serializable (plain dicts/lists)."""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    overrides: dict[str, Any]                      # per-run overrides from the Find Jobs page
    search_settings: dict[str, Any]
    profile: dict[str, Any] | None
    resume_available: bool
    candidate_skills: list[str]
    search_query: dict[str, Any]
    enabled_sources: list[str]
    source_results: list[dict[str, Any]]          # {source, count, status, error}
    raw_listings: list[dict[str, Any]]            # RawListing dumps
    extracted: list[dict[str, Any]]               # listing + derived keys
    screened: list[dict[str, Any]]                # passed hard rules
    rejected: list[dict[str, Any]]                # {listing, outcome, reason}
    to_match: list[dict[str, Any]]
    matched: list[dict[str, Any]]
    retry_round: int
    saved_job_ids: list[str]
    duplicates: int
    fatal: bool
    errors: Annotated[list[str], operator.add]
    log: Annotated[list[str], operator.add]
