"""Resume upload, profile review, resume versions and the list.py skills editor."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config.skills_config import SkillsConfigError, load_skills, merge_skills, save_skills
from database.models import CandidateProfile, ExperienceEntry
from database.repository import ProfileRepository
from frontend.components import chips, empty_state, fmt_time, hero
from rag.resume_parser import ResumeValidationError, extract_profile, parse_resume


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _profile_form(profile: CandidateProfile, key: str, submit_label: str) -> CandidateProfile | None:
    """Editable review form. Returns the corrected profile when submitted."""
    with st.form(f"profile-form-{key}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", profile.name or "")
        location = c2.text_input("Current location", profile.current_location or "")
        headline = st.text_input("Headline", profile.headline or "")
        c3, c4 = st.columns(2)
        total = c3.text_input("Total experience (as stated in the resume)", profile.total_experience or "",
                              help="Leave blank if the resume does not state it.")
        prefs = c4.text_input("Location preferences (comma-separated)", ", ".join(profile.location_preferences))
        skills = st.text_area("Confirmed skills (one per line) — only keep skills you actually have",
                              "\n".join(profile.skills), height=220)
        titles = st.text_area("Target job titles (one per line, optional)", "\n".join(profile.target_titles),
                              height=90)
        certs = st.text_area("Certifications (one per line)", "\n".join(profile.certifications), height=110)
        edu = st.text_area("Education (one per line)", "\n".join(profile.education), height=80)
        st.markdown("**Employment history**")
        exp_df = pd.DataFrame([{"Employer": e.employer, "Title": e.title, "Dates": e.dates, "Location": e.location,
                                "Highlights": " • ".join(e.highlights)} for e in profile.experience]
                              or [{"Employer": "", "Title": "", "Dates": "", "Location": "", "Highlights": ""}])
        exp_edit = st.data_editor(exp_df, num_rows="dynamic", width="stretch", key=f"exp-{key}")
        arrangements = st.multiselect("Preferred work arrangements", ["remote", "hybrid", "office"],
                                      default=profile.work_arrangements)
        submitted = st.form_submit_button(submit_label, type="primary")
    if not submitted:
        return None
    experience = [
        ExperienceEntry(employer=str(r["Employer"] or ""), title=str(r["Title"] or ""), dates=str(r["Dates"] or ""),
                        location=str(r["Location"] or ""),
                        highlights=[h.strip() for h in str(r["Highlights"] or "").split("•") if h.strip()])
        for _, r in exp_edit.iterrows() if any(str(v or "").strip() for v in r.values)
    ]
    skill_list = list(dict.fromkeys(_lines(skills)))
    return profile.model_copy(update={
        "name": name or None, "current_location": location or None, "headline": headline or None,
        "total_experience": total or None, "location_preferences": [p.strip() for p in prefs.split(",") if p.strip()],
        "skills": skill_list,
        "skill_evidence": {k: v for k, v in profile.skill_evidence.items() if k in skill_list},
        "target_titles": _lines(titles), "certifications": _lines(certs), "education": _lines(edu),
        "experience": experience, "work_arrangements": arrangements,
    })


def render(db) -> None:
    hero("Resume & Skills", "Upload your resume, review what was extracted, and manage the skills used for matching.")
    repo = ProfileRepository(db)
    tab_upload, tab_versions, tab_skills = st.tabs(["📤 Upload & review", "🗂️ Resume versions", "🧩 Skills (list.py)"])

    with tab_upload:
        st.caption("Supported: PDF, DOCX, TXT, MD (max 5 MB). The resume is processed locally; only the extracted "
                   "text is stored in the local database. Nothing is sent to Claude unless you allow it in Search "
                   "Settings.")
        upload = st.file_uploader("Upload resume", type=["pdf", "docx", "txt", "md"], accept_multiple_files=False)
        if upload is not None and st.session_state.get("resume-upload-id") != upload.file_id:
            try:
                parsed = parse_resume(upload.name, upload.getvalue())
                st.session_state["resume-draft"] = {"filename": parsed.filename, "text": parsed.text,
                                                    "profile": extract_profile(parsed).model_dump()}
                st.session_state["resume-upload-id"] = upload.file_id
            except ResumeValidationError as exc:
                st.error(str(exc), icon="🚫")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not read this file ({exc.__class__.__name__}: {exc}). Try exporting it as PDF or "
                         "DOCX, and send this message to whoever supports the app if it persists.")

        draft = st.session_state.get("resume-draft")
        if draft:
            profile = CandidateProfile.model_validate(draft["profile"])
            st.success(f"Extracted {len(profile.skills)} skills, {len(profile.experience)} roles and "
                       f"{len(profile.certifications)} certifications from **{draft['filename']}**. Review and "
                       "correct before saving — nothing is added that is not in the document.", icon="🔍")
            with st.expander("Where each skill was found in the resume"):
                for skill, snippet in profile.skill_evidence.items():
                    st.markdown(f"- **{skill}** — “{snippet}”")
            reviewed = _profile_form(profile, "draft", "💾 Save as new resume version and make it active")
            if reviewed is not None:
                pid = repo.add(draft["filename"], reviewed, draft["text"], activate=True)
                st.session_state.pop("resume-draft", None)
                st.toast(f"Saved resume version (profile #{pid}) and set it active.", icon="✅")
                st.rerun()
        elif repo.get_active():
            profile, _, meta = repo.get_active()
            st.info(f"Active resume: **v{meta['resume_version']} · {meta['resume_filename']}** — "
                    f"{len(profile.skills)} confirmed skills, {len(profile.experience)} roles. Upload a new file to "
                    "create another version, or edit the profile under Resume versions.", icon="🟢")
            st.markdown(chips(profile.skills, 40), unsafe_allow_html=True)
        else:
            empty_state("📄", "No resume uploaded yet", "Upload your resume above to build your candidate profile.")

    with tab_versions:
        versions = repo.list()
        if not versions:
            empty_state("🗂️", "No resume versions", "Upload a resume to create the first version.")
        for v in versions:
            c1, c2, c3, c4 = st.columns([4, 2, 1.2, 1.2])
            c1.markdown(f"**v{v['resume_version']}** · {v['resume_filename']}" + ("  🟢 **active**" if v["active"] else ""))
            c2.caption(f"updated {fmt_time(v['updated_at'])}")
            if not v["active"] and c3.button("Make active", key=f"act-{v['profile_id']}"):
                repo.set_active(v["profile_id"])
                st.toast(f"Resume v{v['resume_version']} is now active.", icon="🟢")
                st.rerun()
            if c4.button("Delete", key=f"del-{v['profile_id']}", help="Delete this resume version and its text"):
                repo.delete(v["profile_id"])
                st.toast("Resume version deleted.", icon="🗑️")
                st.rerun()
        active = repo.get_active()
        if active:
            profile, _, meta = active
            st.divider()
            st.subheader(f"Edit active profile (v{meta['resume_version']})")
            edited = _profile_form(profile, f"active-{meta['profile_id']}", "💾 Save profile changes")
            if edited is not None:
                repo.update_profile(meta["profile_id"], edited)
                st.toast("Profile updated. The match profile is recomputed on the next search.", icon="✅")
                st.rerun()

    with tab_skills:
        try:
            configured = load_skills()
            load_error = None
        except SkillsConfigError as exc:
            configured, load_error = [], str(exc)
        if load_error:
            st.error(f"list.py could not be parsed: {load_error}. Fix the file or save a new list below to "
                     "overwrite it.", icon="🧩")
        st.caption("These additional target skills are stored in list.py (valid JSON) and merged with your resume "
                   "skills without duplicates. Every discovered job must match at least one confirmed skill.")
        edited = st.data_editor(pd.DataFrame({"Skill": configured or [""]}), num_rows="dynamic",
                                width="stretch", key="skills-editor")
        if st.button("💾 Save skills to list.py", type="primary"):
            new_skills = [str(s).strip() for s in edited["Skill"].tolist() if str(s or "").strip()]
            try:
                saved = save_skills(new_skills)
                st.toast(f"Saved {len(saved)} skills to list.py.", icon="✅")
                st.rerun()
            except (SkillsConfigError, OSError) as exc:
                st.error(f"Could not save list.py: {exc}")
        active = repo.get_active()
        resume_skills = active[0].skills if active else []
        merged = merge_skills(resume_skills, configured)
        st.markdown(f"**Current match profile — {len(merged)} unique skills** "
                    f"({len(resume_skills)} from resume, {len(configured)} configured)")
        st.markdown(chips(merged, 80), unsafe_allow_html=True)
