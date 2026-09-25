"""Agent Logs - search runs, per-listing outcomes (with rejection reasons) and node logs."""
from __future__ import annotations

import json

import streamlit as st

from database.repository import RunRepository
from frontend.components import empty_state, fmt_time, hero


def render(db) -> None:
    hero("Agent Logs", "Transparent record of every run: what was found, what was rejected and why.")
    runs = RunRepository(db)
    history = runs.list(100)
    if not history:
        empty_state("🧾", "No runs yet", "Logs appear here after you run a search from Find Jobs.")
        return
    labels = {r["run_id"]: f"{fmt_time(r['started_at'])} · {r['status']} · {r['jobs_eligible']} queued"
              for r in history}
    run_id = st.selectbox("Search run", list(labels), format_func=labels.get)
    run = next(r for r in history if r["run_id"] == run_id)
    c = st.columns(5)
    c[0].metric("Discovered", run["jobs_discovered"])
    c[1].metric("Queued", run["jobs_eligible"])
    c[2].metric("Rejected / unverified", run["jobs_rejected"])
    c[3].metric("Duplicates", run["jobs_duplicate"])
    c[4].metric("Status", run["status"])
    st.caption("Sources checked: " + (", ".join(json.loads(run["sources_checked"] or "[]")) or "—"))
    if run.get("error_summary"):
        st.warning(run["error_summary"], icon="⚠️")

    tab1, tab2 = st.tabs(["Listing outcomes", "Node log"])
    with tab1:
        entries = runs.search_log(run_id)
        outcomes = sorted({e["outcome"] for e in entries})
        chosen = st.multiselect("Outcome", outcomes, default=outcomes)
        rows = [{"Outcome": e["outcome"], "Title": e["title"], "Company": e["company"], "Source": e["source_name"],
                 "Reason": e["reason"], "URL": e["url"]} for e in entries if e["outcome"] in chosen]
        st.dataframe(rows, hide_index=True, use_container_width=True,
                     column_config={"URL": st.column_config.LinkColumn("URL", display_text="open ↗")})
    with tab2:
        logs = runs.agent_logs(run_id)
        st.dataframe([{"Time": fmt_time(l["created_at"]), "Node": l["node"], "Level": l["level"],
                       "Message": l["message"]} for l in reversed(logs)], hide_index=True, use_container_width=True)
