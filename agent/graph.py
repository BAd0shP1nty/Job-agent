"""LangGraph workflow for discovery, screening and grounded analysis.

    START → load_profile → build_search_plan → discover_jobs → extract_details
          → apply_hard_rules → record_rejections ─┬─(none passed)→ finalize
                                                   └→ deduplicate ─┬─(all known)→ finalize
                                                                   └→ retrieve_evidence → match_analysis
          → validate_output ─┬─(invalid, budget left)→ retry_or_flag → match_analysis
                             └→ save_pending → finalize → END

Human approval is *not* a graph node: the GUI owns the interaction and calls the
transactional :class:`database.lifecycle.JobLifecycleService`. The database is
the single source of truth for job status; a MemorySaver checkpoint is used only
to let a run be inspected / resumed within the same process.
"""
from __future__ import annotations

from typing import Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent import routing
from agent.nodes import AgentDeps, AgentNodes
from agent.state import AgentState
from database.repository import RunRepository


def build_graph(deps: AgentDeps, checkpointer=None):
    nodes = AgentNodes(deps)
    g = StateGraph(AgentState)
    for name in ("load_profile", "build_search_plan", "discover_jobs", "extract_details", "apply_hard_rules",
                 "record_rejections", "deduplicate", "retrieve_evidence", "match_analysis", "validate_output",
                 "retry_or_flag", "save_pending", "finalize"):
        g.add_node(name, getattr(nodes, name))

    g.add_edge(START, "load_profile")
    g.add_conditional_edges("load_profile", routing.after_profile, ["build_search_plan", "finalize"])
    g.add_conditional_edges("build_search_plan", routing.after_plan, ["discover_jobs", "finalize"])
    g.add_edge("discover_jobs", "extract_details")
    g.add_edge("extract_details", "apply_hard_rules")
    g.add_edge("apply_hard_rules", "record_rejections")
    g.add_conditional_edges("record_rejections", routing.after_rejections, ["deduplicate", "finalize"])
    g.add_conditional_edges("deduplicate", routing.after_dedup, ["retrieve_evidence", "finalize"])
    g.add_edge("retrieve_evidence", "match_analysis")
    g.add_edge("match_analysis", "validate_output")
    g.add_conditional_edges("validate_output", routing.after_validation, ["retry_or_flag", "save_pending"])
    g.add_edge("retry_or_flag", "match_analysis")
    g.add_edge("save_pending", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else MemorySaver())


def run_search(deps: AgentDeps, overrides: dict | None = None,
               on_node: Callable[[str, dict], None] | None = None) -> dict:
    """Execute one search run and return the final state."""
    run_id = RunRepository(deps.db).start()
    graph = build_graph(deps)
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 50}
    state: dict = {"run_id": run_id, "overrides": overrides or {}, "errors": [], "log": []}
    try:
        for update in graph.stream(state, config=config, stream_mode="updates"):
            for node_name, delta in update.items():
                if on_node:
                    on_node(node_name, delta or {})
        final = graph.get_state(config).values
    except Exception as exc:  # noqa: BLE001 - make sure the run is closed out
        RunRepository(deps.db).finish(run_id, status="failed", sources_checked=[], discovered=0, eligible=0,
                                      rejected=0, duplicates=0, errors=[f"Graph error: {exc}"])
        raise
    return dict(final)
