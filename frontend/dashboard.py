"""Dashboard overview - all counts come from the database."""
from __future__ import annotations

import json

import streamlit as st

from database.repository import JobRepository, ProfileRepository, RunRepository, SourceRegistryRepository
from frontend.components import badge, empty_state, esc, fmt_time, hero, relative, stat_card


def render(db) -> None:
    hero("Recruitment command center", "Live overview of discovery, screening and your approval pipeline.")
    counts = JobRepository(db).counts()
    runs = RunRepository(db)
    last = runs.last_finished()
    sources = SourceRegistryRepository(db).list()
    enabled = [s for s in sources if s["enabled"]]

    cards = [
        ("Jobs discovered", counts["discovered"], "listings retrieved across all runs", "#4F46E5"),
        ("Awaiting approval", counts["pending"] + counts["duplicate_review"],
         f"{counts['duplicate_review']} possible duplicates" if counts["duplicate_review"] else "ready to review",
         "#6366F1"),
        ("Selected", counts["selected"], "marked relevant", "#0EA5E9"),
        ("Ignored", counts["ignored"], "suppressed permanently", "#94A3B8"),
        ("Applied", counts["applied_visible"], f"visible · {counts['applied_total']} tracked", "#10B981"),
        ("Last scan", relative(last["finished_at"]) if last else "never",
         fmt_time(last["finished_at"]) if last else "run a search to start", "#F59E0B"),
        ("Enabled sources", len(enabled), ", ".join(s["source_name"] for s in enabled)[:60] or "none", "#06B6D4"),
    ]
    cols = st.columns(4)
    for i, card in enumerate(cards[:4]):
        cols[i].markdown(stat_card(*card), unsafe_allow_html=True)
    st.write("")
    cols = st.columns(4)
    for i, card in enumerate(cards[4:]):
        cols[i].markdown(stat_card(*card), unsafe_allow_html=True)
    with cols[3]:
        active = ProfileRepository(db).get_active()
        if active:
            profile, _, meta = active
            st.markdown(stat_card("Active resume", f"v{meta['resume_version']}", meta["resume_filename"][:40],
                                  "#DB2777"), unsafe_allow_html=True)
        else:
            st.markdown(stat_card("Active resume", "none", "upload in Resume & Skills", "#DB2777"),
                        unsafe_allow_html=True)

    st.write("")
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Recent search runs")
        recent = runs.list(8)
        if not recent:
            empty_state("🔎", "No searches yet", "Open Find Jobs and press Run Job Search to discover opportunities.")
            if st.button("Go to Find Jobs", type="primary"):
                st.session_state["page"] = "Find Jobs"
                st.rerun()
        else:
            rows = []
            for r in recent:
                rows.append({"Started": fmt_time(r["started_at"]), "Status": r["status"],
                             "Sources": ", ".join(json.loads(r["sources_checked"] or "[]")),
                             "Discovered": r["jobs_discovered"], "Queued": r["jobs_eligible"],
                             "Rejected": r["jobs_rejected"], "Duplicates": r["jobs_duplicate"]})
            st.dataframe(rows, width="stretch", hide_index=True)
    with right:
        st.subheader("Source health")
        if not sources:
            empty_state("🔌", "No sources registered", "Open Sources & Connectors.")
        for s in sources:
            st.markdown(
                f"{badge(s['status'].replace('_', ' '), s['status'])} <b>{esc(s['source_name'])}</b> "
                f"{'· enabled' if s['enabled'] else '· disabled'} · last success: {esc(relative(s['last_success_at']))}",
                unsafe_allow_html=True,
            )
        if counts["pending"] + counts["duplicate_review"]:
            st.write("")
            if st.button(f"Review {counts['pending'] + counts['duplicate_review']} pending jobs →", type="primary"):
                st.session_state["page"] = "Pending Approval"
                st.rerun()
