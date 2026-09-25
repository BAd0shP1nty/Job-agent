"""Selected Jobs - table with direct listing links and the Applied status dropdown."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from database.lifecycle import JobLifecycleService, LifecycleError
from database.models import SELECTED_STATUS_OPTIONS
from database.repository import JobRepository
from frontend.components import empty_state, fmt_time, hero

EDITOR_KEY = "selected-editor"


def apply_edits(db, job_ids: list[str], changes: dict) -> list[tuple[str, str]]:
    """Apply data-editor edits (status dropdown / notes) transactionally; returns (icon, message) pairs."""
    lifecycle = JobLifecycleService(db)
    messages = []
    for row_idx, change in changes.items():
        job_id = job_ids[int(row_idx)]
        try:
            if "Notes" in change:
                lifecycle.update_notes(job_id, change["Notes"] or "")
            if change.get("Status") == "Applied":
                expires = lifecycle.mark_applied(job_id)
                messages.append(("✅", f"Marked as applied. Visible in Applied Jobs until {fmt_time(expires, False)}."))
        except LifecycleError as exc:
            messages.append(("⚠️", f"Could not update: {exc}"))
    return messages


def _on_edit(db, job_ids: list[str]) -> None:
    changes = st.session_state.get(EDITOR_KEY, {}).get("edited_rows", {})
    st.session_state["selected-messages"] = apply_edits(db, job_ids, changes)


def render(db) -> None:
    hero("Selected Jobs", "Roles you marked relevant. Set the status to Applied once you have applied yourself.")
    for icon, msg in st.session_state.pop("selected-messages", []):
        st.toast(msg, icon=icon)
    jobs = JobRepository(db).list_selected()
    if not jobs:
        empty_state("⭐", "No selected jobs yet",
                    "Open Pending Approval and click Mark Relevant on roles you want to pursue.")
        if st.button("Go to Pending Approval", type="primary"):
            st.session_state["page"] = "Pending Approval"
            st.rerun()
        return

    query = st.text_input("Filter", placeholder="Search title, company, skill…")
    if query:
        q = query.lower()
        jobs = [j for j in jobs if q in " ".join([j["title"], j["company"], j.get("location") or "",
                                                   *j["matched_skills"]]).lower()]

    frame = pd.DataFrame([{
        "Job title": ("🧪 " if j["is_test_fixture"] else "") + j["title"],
        "Company": j["company"],
        "Status": j.get("selected_status") or "Selected",
        "Listing": j["source_url"],
        "Location": j.get("location") or "",
        "Matching skills": ", ".join(j["matched_skills"]),
        "Match summary": j.get("match_summary") or "",
        "Source": j["source_name"],
        "Discovered": fmt_time(j["ingested_at"], with_time=False),
        "Notes": j.get("application_notes") or "",
    } for j in jobs])
    job_ids = [j["job_id"] for j in jobs]

    st.caption(f"{len(jobs)} selected job(s). Change **Status** to *Applied* to move a job to Applied Jobs. "
               "🧪 = test fixture, not a real vacancy.")
    st.data_editor(
        frame,
        key=EDITOR_KEY,
        on_change=_on_edit,
        args=(db, job_ids),
        hide_index=True,
        use_container_width=True,
        disabled=["Job title", "Company", "Location", "Source", "Discovered", "Listing", "Matching skills",
                  "Match summary"],
        column_config={
            "Listing": st.column_config.LinkColumn("Listing", display_text="Open listing ↗",
                                                   help="Open the original job listing"),
            "Status": st.column_config.SelectboxColumn("Status", options=SELECTED_STATUS_OPTIONS, required=True,
                                                       help="Choose Applied after you have applied"),
            "Match summary": st.column_config.TextColumn("Match summary", width="large"),
            "Notes": st.column_config.TextColumn("Notes", help="Private application notes"),
        },
    )
    with st.expander("Direct links"):
        for j in jobs:
            st.markdown(f"- [{j['title']} — {j['company']}]({j['source_url']})")
