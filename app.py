"""Autopilot Job Hunt Agent - Streamlit entry point.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Autopilot Job Hunt", page_icon="🧭", layout="wide",
                   initial_sidebar_state="expanded")

from config.settings import get_settings  # noqa: E402
from database.connection import get_database  # noqa: E402
from database.lifecycle import JobLifecycleService  # noqa: E402
from database.repository import JobRepository, RunRepository  # noqa: E402
from discovery.registry import SourceRegistry  # noqa: E402
from frontend import (  # noqa: E402
    applied_jobs,
    dashboard,
    find_jobs,
    logs_page,
    pending_jobs,
    resume_skills,
    selected_jobs,
    settings_page,
    sources_page,
)
from frontend.components import inject_css  # noqa: E402

PAGES = [
    ("Dashboard", "📊", dashboard.render),
    ("Find Jobs", "🔎", find_jobs.render),
    ("Pending Approval", "📥", pending_jobs.render),
    ("Selected Jobs", "⭐", selected_jobs.render),
    ("Applied Jobs", "✅", applied_jobs.render),
    ("Resume & Skills", "📄", resume_skills.render),
    ("Search Settings", "⚙️", settings_page.render),
    ("Sources & Connectors", "🔌", sources_page.render),
    ("Agent Logs", "🧾", logs_page.render),
]


@st.cache_resource
def _startup():
    """Runs once per server process: migrate DB, recover interrupted runs, register sources."""
    db = get_database()
    recovered = RunRepository(db).mark_interrupted_runs()
    SourceRegistry(db)
    return db, recovered


def main() -> None:
    inject_css()
    db, recovered = _startup()
    # Query-based expiry already hides old applied jobs; this also purges their stored descriptions.
    JobLifecycleService(db).cleanup_expired_applied()
    if recovered and not st.session_state.get("_recovery_noted"):
        st.toast(f"Recovered after restart: {recovered} interrupted search run(s) were closed.", icon="♻️")
        st.session_state["_recovery_noted"] = True

    st.session_state.setdefault("page", "Dashboard")
    counts = JobRepository(db).counts()
    badges = {
        "Pending Approval": counts["pending"] + counts["duplicate_review"],
        "Selected Jobs": counts["selected"],
        "Applied Jobs": counts["applied_visible"],
    }

    with st.sidebar:
        st.markdown('<div class="jh-brand">🧭 Autopilot Job Hunt</div>', unsafe_allow_html=True)
        st.caption("Discover · Screen · Approve")
        for name, icon, _ in PAGES:
            label = f"{icon}  {name}" + (f"  ({badges[name]})" if name in badges else "")
            is_current = st.session_state["page"] == name
            if st.button(label, key=f"nav-{name}", type="primary" if is_current else "secondary",
                         use_container_width=True, help=f"Open {name}"):
                st.session_state["page"] = name
                st.rerun()
        st.divider()
        settings = get_settings()
        if settings.llm_available:
            st.success(f"Claude: {settings.claude_model}", icon="🤖")
        else:
            st.warning("Claude API key not set – deterministic explanations only.", icon="🔑")
        st.caption("This tool never submits applications. It only discovers, screens and organizes opportunities.")

    page = next((p for p in PAGES if p[0] == st.session_state["page"]), PAGES[0])
    page[2](db)


main()
