"""Applied Jobs - visible for 15 days from each job's original ingestion timestamp."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config.settings import APPLIED_VISIBILITY_DAYS
from database.repository import JobRepository
from frontend.components import days_left, empty_state, fmt_time, hero


def render(db) -> None:
    hero("Applied Jobs", f"Jobs you applied to stay here for {APPLIED_VISIBILITY_DAYS} days from when they were "
                         "first discovered. They never return to the pending queue.")
    rows = JobRepository(db).list_applied_visible()
    st.metric("Active applied records", len(rows))
    if not rows:
        empty_state("✅", "No active applied jobs",
                    "In Selected Jobs, change a job's Status to Applied after you apply. Records expire from this "
                    f"view {APPLIED_VISIBILITY_DAYS} days after original discovery.")
        return
    frame = pd.DataFrame([{
        "Job title": ("🧪 " if r["is_test_fixture"] else "") + r["title"],
        "Company": r["company"],
        "Location": r.get("location") or "",
        "Source": r.get("source_name") or "",
        "Listing": r["source_url"],
        "Matching skills": ", ".join(r["matched_skills"]),
        "Status": r["application_status"],
        "Discovered": fmt_time(r["ingested_at"], with_time=False),
        "Applied": fmt_time(r["applied_at"]),
        "Visible until": fmt_time(r["expires_at"], with_time=False),
        "Days left": days_left(r["expires_at"]),
    } for r in rows])
    st.dataframe(
        frame, hide_index=True, use_container_width=True,
        column_config={
            "Listing": st.column_config.LinkColumn("Listing", display_text="Open listing ↗"),
            "Days left": st.column_config.ProgressColumn("Days left", min_value=0, max_value=APPLIED_VISIBILITY_DAYS,
                                                         format="%d"),
        },
    )
    st.caption("Status reflects what you recorded here. This tool never submits applications on your behalf.")
