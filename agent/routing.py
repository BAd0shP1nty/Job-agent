"""Conditional edges of the agent graph."""
from __future__ import annotations

from agent.nodes import MAX_VALIDATION_ROUNDS
from agent.state import AgentState


def after_profile(state: AgentState) -> str:
    return "finalize" if state.get("fatal") else "build_search_plan"


def after_plan(state: AgentState) -> str:
    return "finalize" if state.get("fatal") else "discover_jobs"


def after_rejections(state: AgentState) -> str:
    """Ineligible / unverified listings end at Record Rejection; the rest continue."""
    return "deduplicate" if state.get("screened") else "finalize"


def after_dedup(state: AgentState) -> str:
    """Already-processed listings are skipped; only new ones get RAG analysis."""
    return "retrieve_evidence" if state.get("to_match") else "finalize"


def after_validation(state: AgentState) -> str:
    invalid = [m for m in state.get("matched", []) if not m.get("valid")]
    if invalid and state.get("retry_round", 0) < MAX_VALIDATION_ROUNDS:
        return "retry_or_flag"
    return "save_pending"
