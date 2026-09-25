"""Builds the agent's dependencies from application settings."""
from __future__ import annotations

from agent.nodes import AgentDeps
from config.settings import get_settings
from database.connection import Database, get_database
from discovery.registry import SourceRegistry


def make_llm_call():
    """Return a callable for Claude, or None when no API key is configured."""
    settings = get_settings()
    if not settings.llm_available:
        return None
    from agent.llm_client import ClaudeClient

    client = ClaudeClient()
    return client.complete_json


def make_deps(db: Database | None = None, progress=None, llm_call="auto") -> AgentDeps:
    db = db or get_database()
    return AgentDeps(
        db=db,
        registry=SourceRegistry(db),
        llm_call=make_llm_call() if llm_call == "auto" else llm_call,
        progress=progress or (lambda node, msg: None),
    )
