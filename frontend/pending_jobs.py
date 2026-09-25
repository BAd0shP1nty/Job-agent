"""Pending approval queue with Mark Relevant / Ignore actions."""
from __future__ import annotations

import streamlit as st

from database.lifecycle import JobLifecycleService, LifecycleError
from database.repository import JobRepository
from frontend.components import badge, chips, empty_state, esc, evidence, fmt_time, hero, safe_url

SORTS = {
    "Relevance score (high → low)": lambda j: -(j.get("match_score") or 0),
    "Newest discovered": lambda j: j.get("ingested_at") or "",
    "Company (A → Z)": lambda j: (j.get("company") or "").lower(),
    "Posting date (newest)": lambda j: j.get("posting_date") or "",
}


def _act(fn, job_id: str, message: str, icon: str) -> None:
    try:
        fn(job_id)
        st.toast(message, icon=icon)
    except LifecycleError as exc:
        st.toast(f"Could not update: {exc}", icon="⚠️")


def render(db) -> None:
    hero("Pending Approval", "Evidence-grounded opportunities waiting for your decision.")
    repo = JobRepository(db)
    lifecycle = JobLifecycleService(db)
    pending = repo.list_by_status("pending")
    duplicates = repo.list_by_status("duplicate_review")

    if not pending and not duplicates:
        empty_state("📭", "Your approval queue is empty",
                    "Run a search from Find Jobs. Only roles that match a confirmed skill and pass the location and "
                    "visa rules appear here.")
        if st.button("Go to Find Jobs", type="primary"):
            st.session_state["page"] = "Find Jobs"
            st.rerun()
        return

    f1, f2, f3, f4 = st.columns([3, 2, 2, 2])
    query = f1.text_input("Search title, company or skill", placeholder="e.g. ITSM, Bangalore, SRE")
    sources = sorted({j["source_name"] for j in pending + duplicates})
    source_filter = f2.multiselect("Source", sources)
    arrangement_filter = f3.multiselect("Work arrangement", ["remote", "hybrid", "office", "unknown"])
    sort_key = f4.selectbox("Sort by", list(SORTS))

    def keep(job: dict) -> bool:
        blob = " ".join([job["title"], job["company"], job.get("location") or "", *job["matched_skills"]]).lower()
        return ((not query or query.lower() in blob)
                and (not source_filter or job["source_name"] in source_filter)
                and (not arrangement_filter or (job.get("work_arrangement") or "unknown") in arrangement_filter))

    reverse = sort_key in ("Newest discovered", "Posting date (newest)")
    shown = sorted([j for j in pending if keep(j)], key=SORTS[sort_key], reverse=reverse)
    st.caption(f"Showing {len(shown)} of {len(pending)} pending jobs")

    for job in shown:
        _job_card(job, lifecycle, duplicate=False)

    if duplicates:
        st.subheader(f"Possible duplicates ({len(duplicates)})")
        st.caption("These look very similar to a job you already have. Decide whether they are distinct openings.")
        for job in duplicates:
            if keep(job):
                _job_card(job, lifecycle, duplicate=True, original=repo.get(job["duplicate_of"])
                          if job.get("duplicate_of") else None)


def _job_card(job: dict, lifecycle: JobLifecycleService, duplicate: bool, original: dict | None = None) -> None:
    details = job.get("match_details") or {}
    color = {"high": "#10B981", "medium": "#F59E0B", "low": "#EF4444"}.get(job.get("confidence") or "", "#6366F1")
    arrangement = job.get("work_arrangement") or "unknown"
    badges = [badge(arrangement if arrangement != "unknown" else "arrangement not stated", arrangement), badge(f"confidence: {job.get('confidence')}",
                                                                    job.get("confidence"))]
    if job.get("is_test_fixture"):
        badges.insert(0, badge("TEST FIXTURE – not a real vacancy", "test"))
    if duplicate:
        badges.append(badge("possible duplicate", "duplicate_review"))
    score = job.get("match_score")
    meta = " · ".join(filter(None, [
        esc(job["company"]), esc(job.get("location") or "Location not stated"), f"source: {esc(job['source_name'])}",
        f"posted {fmt_time(job['posting_date'], with_time=False)}" if job.get("posting_date") else "posting date not verified",
        f"discovered {fmt_time(job['ingested_at'])}",
    ]))
    visa_bits = []
    for label, key in (("Remote from India", "remote_eligibility"), ("Relocation", "relocation_evidence"),
                       ("Visa sponsorship", "visa_sponsorship_evidence")):
        if job.get(key):
            visa_bits.append(f"<b>{label}:</b> “{esc(job[key])}”")
    html = (
        f'<div class="jh-card" style="--c:{color}">'
        f'<h3>{esc(job["title"])}</h3><div class="meta">{meta}</div>'
        f'{"".join(badges)}'
        + (f'{badge(f"relevance {score:.0f}/100", "pending")}' if score is not None else "")
        + f'<div class="summary">{esc(job.get("match_summary") or "")}</div>'
        f'<div><b>Matched skills:</b> {chips(job["matched_skills"])}</div>'
        + (f'<div class="meta"><b>Experience:</b> {esc(job["experience_required"])}</div>'
           if job.get("experience_required") else "")
        + (f'<div class="meta">{" · ".join(visa_bits)}</div>' if visa_bits else "")
        + "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)
    if duplicate and original:
        st.caption(f"Looks similar to: {original['title']} · {original['company']} ({original['status']})")

    url = safe_url(job["source_url"])
    cols = st.columns([1.3, 1.1, 1.1, 1.4, 3]) if duplicate else st.columns([1.3, 1.1, 1.4, 4])
    if cols[0].button("⭐ Mark Relevant", key=f"rel-{job['job_id']}", type="primary",
                      help="Move this job to Selected Jobs"):
        _act(lifecycle.mark_relevant, job["job_id"], f"Moved to Selected: {job['title']}", "⭐")
        st.rerun()
    if cols[1].button("🚫 Ignore", key=f"ign-{job['job_id']}",
                      help="Remove permanently; a fingerprint prevents it from reappearing"):
        _act(lifecycle.ignore, job["job_id"], f"Ignored: {job['title']}", "🚫")
        st.rerun()
    offset = 2
    if duplicate:
        if cols[2].button("↔ Keep as distinct", key=f"keep-{job['job_id']}",
                          help="This is a different opening; move it to the pending queue"):
            _act(lifecycle.keep_as_distinct, job["job_id"], "Moved to pending queue", "↔")
            st.rerun()
        offset = 3
    if url:
        cols[offset].link_button("🔗 Open listing", url, help="Open the original listing in a new tab")

    with st.expander("Full description & match evidence"):
        tab1, tab2, tab3 = st.tabs(["Match evidence", "Job description", "Sources & scoring"])
        with tab1:
            st.markdown(f"**Match status:** {details.get('match_status', 'eligible')} · "
                        f"**Location & arrangement:** {details.get('location_and_arrangement', '')}")
            if details.get("visa_relocation_findings"):
                st.markdown(f"**Visa / relocation findings:** {details['visa_relocation_findings']}")
            if job.get("evidence_source_url"):
                st.markdown(f"**Evidence source:** {job['evidence_source_url']}")
            st.markdown("**Listing evidence**")
            for ev in details.get("job_evidence", [])[:10]:
                st.markdown(evidence(ev["text"]), unsafe_allow_html=True)
            st.markdown("**Resume evidence**")
            if details.get("candidate_evidence"):
                for ev in details["candidate_evidence"][:8]:
                    where = f" ({ev['section']})" if ev.get("section") else ""
                    st.markdown(evidence(ev["text"] + where), unsafe_allow_html=True)
            else:
                st.caption("No resume evidence available (no active resume).")
            if details.get("missing_or_unverified_skills"):
                st.markdown("**Missing or unverified requirements:** "
                            + chips(details["missing_or_unverified_skills"], 20), unsafe_allow_html=True)
            for note in details.get("validation_notes", []):
                st.warning(note, icon="🛡️")
        with tab2:
            st.text(job.get("job_description") or "")
        with tab3:
            st.markdown("**All source listings for this job**")
            for src in job.get("sources", []):
                link = safe_url(src["source_url"])
                st.markdown(f"- {esc(src['source_name'])}: {link or esc(src['source_url'])}")
            st.markdown(f"**Internal relevance score:** {score if score is not None else '—'} "
                        "(documented criteria — not a hiring probability)")
            st.json(details.get("relevance_breakdown", {}))
            st.markdown("**Confidence basis (evidence completeness)**")
            for line in details.get("confidence_basis", []):
                st.markdown(f"- {line}")
            st.caption("Explanation generated by " + ("Claude (validated against evidence)"
                                                      if details.get("llm_used") else "deterministic rules"))
    st.write("")
