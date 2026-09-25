"""Search Settings - persisted preferences, scoring weights, privacy and data retention."""
from __future__ import annotations

import streamlit as st

from config.settings import get_settings
from database.lifecycle import JobLifecycleService
from database.repository import SettingsRepository
from frontend.components import hero
from rag.embeddings import check_embedding_setup


def render(db) -> None:
    hero("Search Settings", "Preferences are saved to the local database. Changing them never deletes selected or "
                            "applied jobs.")
    repo = SettingsRepository(db)
    s = repo.search_settings()
    env = get_settings()

    with st.form("settings-form"):
        st.subheader("Eligibility preferences")
        c1, c2 = st.columns(2)
        bangalore_only = c1.toggle("Bangalore-only for India roles", value=bool(s["bangalore_only"]),
                                   help="When off, India roles outside Bangalore are kept.")
        remote_only = c2.toggle("Remote-only", value=bool(s["remote_only"]))
        st.caption("Overseas roles always require explicit remote-from-India, relocation or visa-sponsorship "
                   "evidence. These rules are enforced in code and cannot be overridden by the model.")

        st.subheader("Internal relevance score")
        st.caption("Score = 100 × weighted average of the components below. It ranks the queue and is **not** a "
                   "probability of being interviewed or hired.")
        w = s["relevance_weights"]
        w1, w2, w3, w4 = st.columns(4)
        weights = {
            "skill_coverage": w1.number_input("Skill coverage", 0.0, 1.0, float(w["skill_coverage"]), 0.05),
            "title_alignment": w2.number_input("Title alignment", 0.0, 1.0, float(w["title_alignment"]), 0.05),
            "evidence_completeness": w3.number_input("Evidence completeness", 0.0, 1.0,
                                                     float(w["evidence_completeness"]), 0.05),
            "recency": w4.number_input("Recency", 0.0, 1.0, float(w["recency"]), 0.05),
        }

        st.subheader("Claude & privacy")
        use_llm = st.toggle("Use Claude for match explanations", value=bool(s["use_llm"]),
                            disabled=not env.llm_available,
                            help="Requires ANTHROPIC_API_KEY. Hard eligibility rules never depend on the model.")
        allow_default = env.allow_resume_to_llm if s["allow_resume_to_llm"] is None else bool(s["allow_resume_to_llm"])
        allow_resume = st.toggle("Allow resume excerpts to be sent to Claude", value=allow_default,
                                 help="Off: only skill names and job-listing passages are sent. On: the few resume "
                                      "passages retrieved as evidence are also sent. The full resume is never sent.")
        saved = st.form_submit_button("💾 Save settings", type="primary")
    if saved:
        repo.save_search_settings({"bangalore_only": bangalore_only, "remote_only": remote_only,
                                   "relevance_weights": weights, "use_llm": use_llm,
                                   "allow_resume_to_llm": allow_resume})
        st.toast("Settings saved.", icon="✅")

    st.subheader("Retrieval setup check")
    status = check_embedding_setup(env.embedding_backend, env.embedding_model)
    (st.success if status.ok else st.warning)(
        f"Embeddings: {status.message}. Vector store: {'ChromaDB' if env.use_chroma else 'in-memory (sufficient for one resume)'}.")
    st.caption(f"Claude model: {env.claude_model} · API key {'configured' if env.llm_available else 'not set'} "
               "(read from the environment, never stored in the database).")

    st.subheader("Data retention")
    st.caption("Resume text lives only in the local SQLite database. Delete resume versions in Resume & Skills.")
    keep = st.checkbox("Keep suppression fingerprints (recommended, prevents ignored/applied jobs from returning)",
                       value=True)
    confirm = st.text_input("Type DELETE to remove all discovered jobs, selections, applied records and logs")
    if st.button("🗑️ Delete job data", disabled=confirm != "DELETE"):
        JobLifecycleService(db).purge_all_job_data(keep_suppression=keep)
        st.toast("Job data deleted.", icon="🗑️")
        st.rerun()
