"""Find Jobs - search controls and the Run Job Search action."""
from __future__ import annotations

import streamlit as st

from agent.graph import run_search
from agent.service import make_deps
from database.repository import ProfileRepository, SettingsRepository
from discovery.registry import SourceRegistry
from frontend.components import badge, esc, hero

LOCATION_OPTIONS = ["Bangalore", "Hyderabad", "Pune", "Mumbai", "Delhi NCR", "Chennai", "Kolkata", "Ahmedabad",
                    "Kochi", "India (any)", "United Kingdom", "Ireland", "Germany", "France", "Netherlands",
                    "Belgium", "Spain", "Poland", "Switzerland", "Nordics", "United Arab Emirates", "Saudi Arabia",
                    "Qatar", "South Africa", "Remote (India-eligible)"]
EMPLOYMENT_OPTIONS = ["full-time", "contract", "part-time", "temporary"]
SENIORITY_OPTIONS = ["mid", "senior", "lead", "manager", "director", "head"]
NODE_LABELS = {
    "load_profile": "Loading candidate profile", "build_search_plan": "Building search plan",
    "discover_jobs": "Discovering jobs", "extract_details": "Extracting listing details",
    "apply_hard_rules": "Applying skill, location & visa rules", "record_rejections": "Recording rejections",
    "deduplicate": "Deduplicating against history", "retrieve_evidence": "Retrieving resume evidence",
    "match_analysis": "Grounded match analysis", "validate_output": "Validating evidence & output",
    "retry_or_flag": "Retrying invalid results", "save_pending": "Saving to the approval queue",
    "finalize": "Finishing run",
}


def render(db) -> None:
    hero("Find Jobs", "Run the agent across your enabled sources, or add a job you found yourself. Only verified, "
                      "skill-matched roles reach your queue.")
    tab_search, tab_manual = st.tabs(["🔎 Search sources", "➕ Add a job you found"])
    with tab_search:
        _render_search(db)
    with tab_manual:
        _render_manual(db)


def _render_manual(db) -> None:
    st.caption("Found a job on LinkedIn, Naukri, a company site or anywhere else? Paste it here. The page itself is "
               "not fetched (those portals don't permit it); the job is checked against the same skill, location and "
               "visa rules, matched to your resume with evidence, and de-duplicated like any other job.")
    with st.form("manual-job-form", clear_on_submit=False):
        url = st.text_input("Job link *", placeholder="https://www.linkedin.com/jobs/view/…")
        c1, c2 = st.columns(2)
        title = c1.text_input("Job title *")
        company = c2.text_input("Company *")
        c3, c4, c5 = st.columns(3)
        location = c3.text_input("Location (as stated in the listing)", placeholder="e.g. Bengaluru, Karnataka, India")
        arrangement = c4.selectbox("Work arrangement (as stated)", ["not stated", "remote", "hybrid", "onsite"])
        posted = c5.date_input("Posting date (if shown)", value=None, format="DD/MM/YYYY")
        description = st.text_area("Full job description *", height=260,
                                   help="Copy the whole description, including any visa, relocation or remote-work "
                                        "terms. Only this text is used as evidence.")
        submitted = st.form_submit_button("🧪 Check this job", type="primary", use_container_width=True)
    if not submitted:
        return
    from agent.manual import ManualJobError, build_manual_listing, screen_manual_job

    try:
        listing = build_manual_listing(url=url, title=title, company=company, location=location,
                                       description=description, work_arrangement=arrangement, posting_date=posted)
    except ManualJobError as exc:
        st.error(str(exc), icon="✏️")
        return
    with st.spinner("Screening and matching…"):
        try:
            result = screen_manual_job(make_deps(db), listing)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not check the job: {exc}")
            return
    messages = {
        "pending": ("success", "Eligible – added to Pending Approval."),
        "duplicate_review": ("warning", "Eligible, but it looks very similar to a job you already have – added to "
                                        "Possible duplicates in Pending Approval."),
        "duplicate": ("info", "You already have this job (or ignored/applied to it earlier), so it was not added again."),
        "unverified": ("warning", "Not added: key eligibility information is ambiguous or missing."),
        "rejected": ("error", "Not added: it does not meet your eligibility rules."),
        "flagged": ("warning", "Not added: the match could not be validated."),
    }
    kind, text = messages.get(result.outcome, ("info", f"Outcome: {result.outcome}"))
    getattr(st, kind)(f"**{text}**  \nReason: {result.reason}")
    if result.outcome in ("pending", "duplicate_review"):
        if st.button("Open Pending Approval →", type="primary"):
            st.session_state["page"] = "Pending Approval"
            st.rerun()


def _render_search(db) -> None:
    repo = SettingsRepository(db)
    s = repo.search_settings()
    registry = SourceRegistry(db)
    entries = registry.entries()
    all_sources = [e["source_name"] for e in entries if e.get("source_name")]
    enabled_now = [e["source_name"] for e in entries if e.get("enabled")]

    if not ProfileRepository(db).get_active():
        st.info("No active resume yet. The search will use only the skills in list.py. Upload a resume in "
                "**Resume & Skills** for resume-grounded matching.", icon="📄")

    with st.form("search-form"):
        c1, c2 = st.columns(2)
        with c1:
            locations = st.multiselect("Target locations", LOCATION_OPTIONS,
                                       default=[l for l in s["target_locations"] if l in LOCATION_OPTIONS])
            titles = st.text_area("Target job titles (one per line)", "\n".join(s["target_titles"]), height=130)
            employment = st.multiselect("Employment type", EMPLOYMENT_OPTIONS,
                                        default=[e for e in s["employment_types"] if e in EMPLOYMENT_OPTIONS])
            seniority = st.multiselect("Seniority level", SENIORITY_OPTIONS,
                                       default=[x for x in s["seniority_levels"] if x in SENIORITY_OPTIONS])
        with c2:
            sources = st.multiselect("Enabled sources", all_sources, default=enabled_now,
                                     help="Enable/disable permanently in Sources & Connectors.")
            date_posted = st.select_slider("Date posted (max age, days)", options=[1, 3, 7, 14, 30, 60, 90],
                                           value=s["date_posted_days"] if s["date_posted_days"] in
                                           [1, 3, 7, 14, 30, 60, 90] else 30)
            min_score = st.slider("Minimum internal relevance score", 0, 100, int(s["min_relevance_score"]),
                                  help="Documented relevance score, not a hiring probability.")
            max_results = st.number_input("Maximum results per search (per source)", 5, 200,
                                          int(s["max_results_per_search"]), step=5)
            t1, t2 = st.columns(2)
            remote_only = t1.toggle("Remote-only", value=bool(s["remote_only"]))
            bangalore_only = t2.toggle("Bangalore-only (India roles)", value=bool(s["bangalore_only"]))
        submitted = st.form_submit_button("🚀  Run Job Search", type="primary", use_container_width=True)

    if not submitted:
        _render_last_result()
        return

    values = {
        "target_locations": locations, "target_titles": [t.strip() for t in titles.splitlines() if t.strip()],
        "employment_types": employment, "seniority_levels": seniority, "date_posted_days": int(date_posted),
        "min_relevance_score": int(min_score), "max_results_per_search": int(max_results),
        "remote_only": remote_only, "bangalore_only": bangalore_only,
    }
    repo.save_search_settings(values)  # persisted; never deletes selected/applied jobs
    if not sources:
        st.warning("Select at least one source to search.")
        return

    progress = st.progress(0.0, text="Starting…")
    total = len(NODE_LABELS)
    seen: list[str] = []
    with st.status("Agent running…", expanded=True) as status:
        def on_progress(node: str, msg: str) -> None:
            if msg == "started":
                seen.append(node)
                progress.progress(min(len(seen) / total, 1.0), text=NODE_LABELS.get(node, node))
            else:
                status.write(f"**{NODE_LABELS.get(node, node)}** – {msg}")

        try:
            deps = make_deps(db, progress=on_progress)
            final = run_search(deps, overrides={"enabled_sources_override": sources})
        except Exception as exc:  # noqa: BLE001
            status.update(label="Search failed", state="error")
            st.error(f"The search could not complete: {exc}. Previously saved jobs are unaffected.")
            return
        progress.progress(1.0, text="Done")
        errs = final.get("errors") or []
        status.update(label="Search finished" + (" with source errors" if errs else ""),
                      state="error" if final.get("fatal") else "complete", expanded=False)
    st.session_state["last_search"] = {
        "saved": len(final.get("saved_job_ids", [])), "discovered": len(final.get("raw_listings", [])),
        "rejected": len(final.get("rejected", [])), "duplicates": final.get("duplicates", 0),
        "sources": final.get("source_results", []), "errors": errs, "run_id": final.get("run_id"),
    }
    _render_last_result()


def _render_last_result() -> None:
    res = st.session_state.get("last_search")
    if not res:
        return
    st.subheader("Last run")
    c = st.columns(4)
    c[0].metric("Listings discovered", res["discovered"])
    c[1].metric("Added to approval queue", res["saved"])
    c[2].metric("Rejected / unverified", res["rejected"])
    c[3].metric("Duplicates skipped", res["duplicates"])
    for src in res["sources"]:
        st.markdown(f"{badge(src['status'].replace('_', ' '), src['status'])} **{esc(src['source'])}** – "
                    f"{src['count']} listings" + (f" · {esc(src['error'])}" if src.get("error") else ""),
                    unsafe_allow_html=True)
    if res["saved"]:
        if st.button(f"Review {res['saved']} new jobs →", type="primary"):
            st.session_state["page"] = "Pending Approval"
            st.rerun()
    else:
        st.caption("No new eligible jobs this time. See Agent Logs for the reason each listing was rejected.")
