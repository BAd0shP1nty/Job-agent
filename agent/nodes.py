"""LangGraph nodes. Each node is a small, independently testable function of the state.

Nodes receive their dependencies through :class:`AgentDeps` rather than globals so
tests can inject an in-memory database, mocked connectors and a fake LLM.
"""
from __future__ import annotations

import functools
import traceback
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from agent.prompts import build_match_user_prompt
from agent.state import AgentState
from config.logging_config import get_logger
from config.settings import get_settings, parse_iso, utcnow
from config.skills_config import SkillsConfigError, load_skills, merge_skills
from database.connection import Database
from database.models import CandidateProfile, LocationDecision, MatchResult, RawListing, SkillMatch
from database.repository import JobRepository, ProfileRepository, RunRepository, SettingsRepository
from discovery.base import SearchQuery
from discovery.registry import SourceRegistry
from rag.matcher import EvidenceBundle, MatchInputs, build_match_result, gather_evidence, run_llm_explanation
from rag.retrieval import ResumeIndex, RetrievedEvidence
from rag.chunking import Chunk
from screening.deduplication import (
    FUZZY_TITLE_THRESHOLD,
    canonicalize_url,
    job_fingerprint,
    loose_key,
    normalize_company,
    normalize_location,
    sha256,
    title_similarity,
)
from screening.location_filter import screen_location
from screening.skill_filter import match_skills, passes_skill_filter

log = get_logger("agent")

MAX_VALIDATION_ROUNDS = 1


@dataclass
class AgentDeps:
    db: Database
    registry: SourceRegistry
    llm_call: Callable[[list[dict]], str] | None = None   # returns JSON text; None -> deterministic only
    progress: Callable[[str, str], None] = lambda node, msg: None
    skills_path: Any = None
    cache: dict[str, Any] = field(default_factory=dict)   # per-run objects (e.g. resume index), not persisted


def node(name: str, fatal_on_error: bool = False):
    """Wrap a node with structured logging and error capture."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(self: "AgentNodes", state: AgentState) -> dict:
            runs = RunRepository(self.deps.db)
            run_id = state.get("run_id")
            self.deps.progress(name, "started")
            try:
                update = fn(self, state) or {}
            except Exception as exc:  # noqa: BLE001
                msg = f"{name} failed: {exc.__class__.__name__}: {exc}"
                log.error(msg + "\n" + traceback.format_exc(limit=3))
                runs.log(run_id, name, "ERROR", msg)
                self.deps.progress(name, f"error: {exc}")
                return {"errors": [msg], "fatal": fatal_on_error or state.get("fatal", False)}
            for line in update.pop("_log", []):
                runs.log(run_id, name, "INFO", line)
                self.deps.progress(name, line)
            return update

        return wrapper

    return decorator


class AgentNodes:
    def __init__(self, deps: AgentDeps):
        self.deps = deps
        self.jobs = JobRepository(deps.db)
        self.runs = RunRepository(deps.db)

    # 1 ------------------------------------------------------------------------
    @node("load_profile", fatal_on_error=True)
    def load_profile(self, state: AgentState) -> dict:
        settings_repo = SettingsRepository(self.deps.db)
        search_settings = {**settings_repo.search_settings(), **(state.get("overrides") or {})}
        active = ProfileRepository(self.deps.db).get_active()
        try:
            configured = load_skills(self.deps.skills_path)
        except SkillsConfigError as exc:
            return {"errors": [f"Skills configuration error: {exc}"], "fatal": True}
        lines = []
        if active:
            profile, resume_text, meta = active
            self.deps.cache["resume_text"] = resume_text
            skills = merge_skills(profile.skills, configured)
            lines.append(f"Active resume v{meta['resume_version']} loaded; {len(profile.skills)} resume skills, "
                         f"{len(configured)} configured skills -> {len(skills)} unique.")
            profile_dict = profile.model_dump()
        else:
            skills = merge_skills([], configured)
            profile_dict = None
            lines.append("No active resume - using configured skills only; resume evidence will be unavailable.")
        if not skills:
            return {"errors": ["No confirmed skills available; add skills or upload a resume."], "fatal": True}
        return {"search_settings": search_settings, "profile": profile_dict, "resume_available": bool(active),
                "candidate_skills": skills, "fatal": False, "_log": lines}

    # 2 ------------------------------------------------------------------------
    @node("build_search_plan", fatal_on_error=True)
    def build_search_plan(self, state: AgentState) -> dict:
        s = state["search_settings"]
        profile = state.get("profile") or {}
        titles = list(dict.fromkeys((s.get("target_titles") or []) + (profile.get("target_titles") or [])))
        query = SearchQuery(
            keywords=titles or ["service delivery manager"],
            skills=state["candidate_skills"],
            locations=["Bangalore"] if s.get("bangalore_only") else list(s.get("target_locations") or []),
            remote_only=bool(s.get("remote_only")),
            max_results=int(s.get("max_results_per_search") or 50),
            date_posted_days=s.get("date_posted_days"),
        )
        enabled = [n for n in (s.get("enabled_sources_override") or self.deps.registry.enabled_names())
                   if n in self.deps.registry.classes]
        return {"search_query": query.__dict__, "enabled_sources": enabled,
                "_log": [f"Plan: {len(query.keywords)} titles, {len(query.skills)} skills, sources: "
                         f"{', '.join(enabled) or 'none enabled'}."]}

    # 3 ------------------------------------------------------------------------
    @node("discover_jobs")
    def discover_jobs(self, state: AgentState) -> dict:
        query = SearchQuery(**state["search_query"])
        listings: list[dict] = []
        results, lines, errors = [], [], []
        for name in state.get("enabled_sources", []):
            self.deps.progress("discover_jobs", f"Searching {name}…")
            res = self.deps.registry.run_source(name, query)
            results.append({"source": name, "count": len(res.listings), "status": res.status, "error": res.error})
            if res.error:
                errors.append(f"{name}: {res.error}")
                lines.append(f"{name}: {res.status} - {res.error}")
            else:
                lines.append(f"{name}: {len(res.listings)} listings")
            listings.extend(l.model_dump() for l in res.listings)
        return {"raw_listings": listings, "source_results": results, "errors": errors, "_log": lines}

    # 4 ------------------------------------------------------------------------
    @node("extract_details")
    def extract_details(self, state: AgentState) -> dict:
        s = state["search_settings"]
        max_age = s.get("date_posted_days")
        extracted, rejected, seen = [], list(state.get("rejected", [])), set()
        for raw in state.get("raw_listings", []):
            try:
                listing = RawListing.model_validate(raw)
            except Exception as exc:  # noqa: BLE001
                rejected.append({"listing": _brief(raw), "outcome": "rejected", "reason": f"malformed listing: {exc}"})
                continue
            if not (listing.title.strip() and listing.company.strip() and listing.source_url.startswith("http")):
                rejected.append({"listing": _brief(raw), "outcome": "rejected",
                                 "reason": "malformed listing (missing title, company or URL)"})
                continue
            canonical = canonicalize_url(listing.source_url)
            if canonical in seen:
                continue
            seen.add(canonical)
            posted = parse_iso(listing.posting_date)
            if max_age and posted and utcnow() - posted > timedelta(days=int(max_age)):
                rejected.append({"listing": _brief(raw), "outcome": "rejected",
                                 "reason": f"posted more than {max_age} days ago"})
                continue
            extracted.append({"listing": listing.model_dump(), "canonical_url": canonical,
                              "canonical_url_hash": sha256(canonical)})
        return {"extracted": extracted, "rejected": rejected,
                "_log": [f"{len(extracted)} listings extracted, {len(rejected)} rejected so far."]}

    # 5 ------------------------------------------------------------------------
    @node("apply_hard_rules")
    def apply_hard_rules(self, state: AgentState) -> dict:
        s = state["search_settings"]
        skills = state["candidate_skills"]
        screened, rejected = [], list(state.get("rejected", []))
        for item in state.get("extracted", []):
            listing = RawListing.model_validate(item["listing"])
            haystack = f"{listing.title}\n{listing.description}"
            matches = match_skills(skills, haystack, listing.source_url)
            if not passes_skill_filter(matches):
                rejected.append({"listing": _brief(item["listing"]), "outcome": "rejected",
                                 "reason": "no confirmed skill from the resume or list.py appears in the listing"})
                continue
            decision = screen_location(
                title=listing.title, location=listing.location, country_hint=listing.country,
                description=listing.description, work_arrangement_hint=listing.work_arrangement_hint,
                remote_location_field=listing.remote_location_field, source_url=listing.source_url,
                bangalore_only=bool(s.get("bangalore_only")), remote_only=bool(s.get("remote_only")),
            )
            if decision.status != "eligible":
                rejected.append({"listing": _brief(item["listing"]),
                                 "outcome": "unverified" if decision.status == "requires_verification" else "rejected",
                                 "reason": decision.reason})
                continue
            country = decision.country
            screened.append({
                **item,
                "skill_matches": [m.model_dump() for m in matches],
                "location_decision": decision.model_dump(),
                "fingerprint": job_fingerprint(listing.title, listing.company, listing.location, country,
                                               listing.requisition_id),
                "loose_key": loose_key(listing.title, listing.company, listing.location, country),
                "country": country,
            })
        return {"screened": screened, "rejected": rejected,
                "_log": [f"{len(screened)} passed hard rules; {len(rejected)} rejected/unverified in total."]}

    # 6 ------------------------------------------------------------------------
    @node("record_rejections")
    def record_rejections(self, state: AgentState) -> dict:
        for r in state.get("rejected", []):
            b = r["listing"]
            self.runs.log_listing(state["run_id"], source_name=b.get("source_name", ""), title=b.get("title", ""),
                                  company=b.get("company", ""), url=b.get("source_url", ""), outcome=r["outcome"],
                                  reason=r["reason"])
        return {"_log": [f"Recorded {len(state.get('rejected', []))} rejection/unverified entries."]}

    # 7 ------------------------------------------------------------------------
    @node("deduplicate")
    def deduplicate(self, state: AgentState) -> dict:
        to_match: list[dict] = []
        duplicates = 0
        batch_by_loose: dict[str, dict] = {}
        for item in state.get("screened", []):
            listing = item["listing"]
            check = self.jobs.check_duplicate(
                canonical_url_hash=item["canonical_url_hash"], fingerprint=item["fingerprint"],
                loose=item["loose_key"], has_requisition_id=bool(listing.get("requisition_id")),
                title=listing["title"], company=listing["company"], location=listing["location"],
                country=item.get("country"),
            )
            if check.kind in ("suppressed", "existing"):
                duplicates += 1
                if check.kind == "existing" and check.job_id:
                    self.jobs.add_source(check.job_id, listing["source_name"], listing["source_url"])
                self.runs.log_listing(state["run_id"], source_name=listing["source_name"], title=listing["title"],
                                      company=listing["company"], url=listing["source_url"], outcome="duplicate",
                                      reason=check.reason)
                continue
            # Same job twice within this run (e.g. two portals) -> one card, extra source URL.
            sibling = batch_by_loose.get(item["loose_key"])
            if sibling and not (sibling["listing"].get("requisition_id") and listing.get("requisition_id")):
                sibling.setdefault("extra_sources", []).append(
                    {"source_name": listing["source_name"], "source_url": listing["source_url"]})
                duplicates += 1
                self.runs.log_listing(state["run_id"], source_name=listing["source_name"], title=listing["title"],
                                      company=listing["company"], url=listing["source_url"], outcome="duplicate",
                                      reason="same job found on another source in this run (merged)")
                continue
            if check.kind == "possible_duplicate":
                item = {**item, "duplicate_of": check.job_id}
            else:
                fuzzy = next((o for o in batch_by_loose.values()
                              if normalize_company(o["listing"]["company"]) == normalize_company(listing["company"])
                              and normalize_location(o["listing"]["location"], o.get("country"))
                              == normalize_location(listing["location"], item.get("country"))
                              and title_similarity(o["listing"]["title"], listing["title"]) >= FUZZY_TITLE_THRESHOLD),
                             None)
                if fuzzy is not None:
                    item = {**item, "duplicate_of_key": fuzzy["fingerprint"]}
            batch_by_loose[item["loose_key"]] = item
            to_match.append(item)
        return {"to_match": to_match, "duplicates": duplicates,
                "_log": [f"{len(to_match)} new listings; {duplicates} duplicates skipped or merged."]}

    # 8 ------------------------------------------------------------------------
    @node("retrieve_evidence")
    def retrieve_evidence(self, state: AgentState) -> dict:
        settings = get_settings()
        profile = state.get("profile") or {}
        resume_text = self.deps.cache.get("resume_text")
        index = None
        if resume_text:
            index = ResumeIndex(resume_text, profile.get("resume_sections") or None, backend=settings.embedding_backend,
                                model_name=settings.embedding_model)
        out = []
        for item in state.get("to_match", []):
            inputs = _inputs(item, state["candidate_skills"])
            bundle = gather_evidence(inputs, index, backend=settings.embedding_backend)
            out.append({**item, "evidence": _bundle_to_dict(bundle)})
        return {"to_match": out, "_log": [f"Retrieved resume/listing evidence for {len(out)} listings."]}

    # 9 ------------------------------------------------------------------------
    @node("match_analysis")
    def match_analysis(self, state: AgentState) -> dict:
        s = state["search_settings"]
        settings = get_settings()
        allow_resume = s.get("allow_resume_to_llm")
        allow_resume = settings.allow_resume_to_llm if allow_resume is None else bool(allow_resume)
        profile = CandidateProfile.model_validate(state.get("profile") or {})
        retry_round = state.get("retry_round", 0)
        previous = {m["fingerprint"]: m for m in state.get("matched", [])}
        matched, lines = [], []
        for item in state.get("to_match", []):
            prior = previous.get(item["fingerprint"])
            if prior is not None and prior.get("valid"):
                matched.append(prior)
                continue
            force_deterministic = prior is not None  # a retry falls back to the deterministic explanation
            inputs = _inputs(item, state["candidate_skills"])
            bundle = _bundle_from_dict(item["evidence"])
            llm_output, notes = None, []
            use_llm = bool(s.get("use_llm", True)) and self.deps.llm_call is not None and not force_deterministic
            if use_llm:
                resume_passages = [r.chunk.text for r in bundle.resume] if allow_resume else []
                job_passages = [m.evidence.text for m in inputs.skill_matches] + [r.chunk.text for r in bundle.job]
                prompt = build_match_user_prompt(
                    title=inputs.title, company=inputs.company, location=inputs.location,
                    confirmed_skills=[m.skill for m in inputs.skill_matches], resume_passages=resume_passages or None,
                    job_passages=job_passages, candidate_skill_names=inputs.candidate_skills)
                try:
                    outcome, attempts = run_llm_explanation(
                        self.deps.llm_call, prompt, allowed_skills=[m.skill for m in inputs.skill_matches],
                        resume_passages=resume_passages, job_passages=job_passages)
                    if outcome.ok:
                        llm_output = outcome.output
                        notes += [f"Dropped unsupported model claim: {d}" for d in outcome.dropped]
                    else:
                        notes.append(f"Model explanation rejected after {attempts} attempt(s): "
                                     + "; ".join(outcome.errors) + " - deterministic summary shown.")
                except Exception as exc:  # noqa: BLE001 - LLM outage must not stop the pipeline
                    notes.append(f"Model unavailable ({exc}); deterministic summary shown.")
            elif force_deterministic:
                notes.append("Flagged: earlier explanation failed validation; deterministic summary shown.")
            result = build_match_result(inputs, bundle, profile=profile,
                                        target_titles=s.get("target_titles") or [],
                                        weights=s.get("relevance_weights"), llm_output=llm_output, llm_notes=notes)
            matched.append({"fingerprint": item["fingerprint"], "match": result.model_dump(), "valid": None,
                            "flagged": force_deterministic})
        n_llm = sum(1 for m in matched if m["match"]["llm_used"])
        lines.append(f"Analysed {len(matched)} listings ({n_llm} with Claude explanations, round {retry_round}).")
        return {"matched": matched, "_log": lines}

    # 10 -----------------------------------------------------------------------
    @node("validate_output")
    def validate_output(self, state: AgentState) -> dict:
        resume_text = self.deps.cache.get("resume_text") or ""
        by_fp = {i["fingerprint"]: i for i in state.get("to_match", [])}
        checked, invalid = [], 0
        for m in state.get("matched", []):
            if m.get("valid"):
                checked.append(m)
                continue
            item = by_fp[m["fingerprint"]]
            problems = validate_match(MatchResult.model_validate(m["match"]), item, resume_text)
            if problems:
                invalid += 1
            checked.append({**m, "valid": not problems, "problems": problems})
        return {"matched": checked, "_log": [f"Validated {len(checked)} results; {invalid} invalid."]}

    # 11 -----------------------------------------------------------------------
    @node("retry_or_flag")
    def retry_or_flag(self, state: AgentState) -> dict:
        return {"retry_round": state.get("retry_round", 0) + 1,
                "_log": ["Re-running analysis for invalid results with the deterministic fallback."]}

    # 12 -----------------------------------------------------------------------
    @node("save_pending")
    def save_pending(self, state: AgentState) -> dict:
        s = state["search_settings"]
        min_score = float(s.get("min_relevance_score") or 0)
        matched = {m["fingerprint"]: m for m in state.get("matched", [])}
        key_to_job: dict[str, str] = {}
        saved, skipped = [], 0
        for item in state.get("to_match", []):
            m = matched.get(item["fingerprint"])
            listing = item["listing"]
            if m is None or not m.get("valid"):
                self.runs.log_listing(state["run_id"], source_name=listing["source_name"], title=listing["title"],
                                      company=listing["company"], url=listing["source_url"], outcome="flagged",
                                      reason="match output failed validation: " + "; ".join((m or {}).get("problems", [])))
                continue
            match = MatchResult.model_validate(m["match"])
            if (match.relevance_score or 0) < min_score:
                skipped += 1
                self.runs.log_listing(state["run_id"], source_name=listing["source_name"], title=listing["title"],
                                      company=listing["company"], url=listing["source_url"], outcome="below_threshold",
                                      reason=f"relevance {match.relevance_score} < minimum {min_score}")
                continue
            decision = LocationDecision.model_validate(item["location_decision"])
            duplicate_of = item.get("duplicate_of") or key_to_job.get(item.get("duplicate_of_key", ""))
            job_id = self.jobs.insert({
                "canonical_url": item["canonical_url"], "canonical_url_hash": item["canonical_url_hash"],
                "fingerprint": item["fingerprint"], "loose_key": item["loose_key"],
                "requisition_id": listing.get("requisition_id"),
                "title": listing["title"], "company": listing["company"], "location": listing["location"],
                "country": decision.country, "work_arrangement": decision.work_arrangement,
                "employment_type": listing.get("employment_type"), "seniority": listing.get("seniority"),
                "experience_required": _experience_required(listing["description"]),
                "job_description": listing["description"], "posting_date": listing.get("posting_date"),
                "source_name": listing["source_name"], "source_url": listing["source_url"],
                "eligibility_status": "eligible",
                "remote_eligibility": decision.remote_eligibility,
                "relocation_evidence": decision.relocation_evidence,
                "visa_sponsorship_evidence": decision.visa_sponsorship_evidence,
                "evidence_source_url": decision.evidence_source_url,
                "match_summary": match.explanation, "match_score": match.relevance_score,
                "matched_skills": match.confirmed_skills, "match_details": match.model_dump(),
                "confidence": match.confidence, "duplicate_of": duplicate_of,
                "is_test_fixture": listing.get("is_test_fixture", False),
                "status": "duplicate_review" if duplicate_of else "pending",
            })
            key_to_job[item["fingerprint"]] = job_id
            for extra in item.get("extra_sources", []):
                self.jobs.add_source(job_id, extra["source_name"], extra["source_url"])
            outcome = "duplicate_review" if duplicate_of else "pending"
            self.runs.log_listing(state["run_id"], source_name=listing["source_name"], title=listing["title"],
                                  company=listing["company"], url=listing["source_url"], outcome=outcome,
                                  reason="eligible; awaiting human approval")
            saved.append(job_id)
        return {"saved_job_ids": saved, "_log": [f"Saved {len(saved)} listings for review ({skipped} below threshold)."]}

    # 13 -----------------------------------------------------------------------
    @node("finalize")
    def finalize(self, state: AgentState) -> dict:
        rejected = state.get("rejected", [])
        status = "failed" if state.get("fatal") else "completed_with_errors" if state.get("errors") else "completed"
        self.runs.finish(
            state["run_id"], status=status,
            sources_checked=[r["source"] for r in state.get("source_results", [])],
            discovered=len(state.get("raw_listings", [])), eligible=len(state.get("saved_job_ids", [])),
            rejected=len(rejected), duplicates=state.get("duplicates", 0), errors=state.get("errors", []),
        )
        return {"_log": [f"Run {status}."]}


# ---------------------------------------------------------------------------
# validation of the final match record (evidence & hard-rule integrity)
# ---------------------------------------------------------------------------


def validate_match(match: MatchResult, item: dict, resume_text: str) -> list[str]:
    problems = []
    if match.match_status != "eligible":
        problems.append("match status differs from the deterministic eligibility decision")
    expected = {m["skill"] for m in item["skill_matches"]}
    if not set(match.confirmed_skills) <= expected:
        problems.append("confirmed skills include a skill without listing evidence")
    if not match.confirmed_skills:
        problems.append("no confirmed skills")
    description = item["listing"]["description"]
    for sm in item["skill_matches"]:
        if sm["evidence"]["text"] not in description and sm["evidence"]["text"] not in item["listing"]["title"]:
            problems.append(f"skill evidence for {sm['skill']} not found in listing")
    for ev in match.candidate_evidence:
        if ev.start is not None and resume_text and resume_text[ev.start:ev.end][:50] != ev.text[:50]:
            problems.append("resume evidence offsets do not match the resume text")
            break
    if match.relevance_score is not None and not 0 <= match.relevance_score <= 100:
        problems.append("relevance score outside 0-100")
    if not match.source_url.startswith("http"):
        problems.append("missing source URL")
    return problems


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _brief(raw: dict) -> dict:
    return {k: str(raw.get(k, ""))[:300] for k in ("source_name", "title", "company", "source_url")}


def _inputs(item: dict, candidate_skills: list[str]) -> MatchInputs:
    listing = item["listing"]
    return MatchInputs(
        job_key=item["fingerprint"][:12], title=listing["title"], company=listing["company"],
        location=listing["location"], description=listing["description"], source_url=listing["source_url"],
        posting_date=listing.get("posting_date"),
        skill_matches=[SkillMatch.model_validate(m) for m in item["skill_matches"]],
        location_decision=LocationDecision.model_validate(item["location_decision"]),
        candidate_skills=candidate_skills,
    )


def _bundle_to_dict(bundle: EvidenceBundle) -> dict:
    def ev(r: RetrievedEvidence) -> dict:
        return {"chunk": r.chunk.__dict__, "score": r.score, "semantic": r.semantic, "keyword": r.keyword,
                "matched_terms": r.matched_terms}

    return {"resume": [ev(r) for r in bundle.resume], "job": [ev(r) for r in bundle.job],
            "required": bundle.required, "missing": bundle.missing, "injection_flags": bundle.injection_flags}


def _bundle_from_dict(d: dict) -> EvidenceBundle:
    def ev(x: dict) -> RetrievedEvidence:
        return RetrievedEvidence(Chunk(**x["chunk"]), x["score"], x["semantic"], x["keyword"], x["matched_terms"])

    return EvidenceBundle(resume=[ev(x) for x in d["resume"]], job=[ev(x) for x in d["job"]],
                          required=d["required"], missing=d["missing"], injection_flags=d["injection_flags"])


def _experience_required(description: str) -> str | None:
    import re

    m = re.search(r"(\d{1,2}\s*\+?\s*(?:-|to)?\s*\d{0,2}\+?\s*years?[^.\n]{0,60}experience)", description, re.I)
    return m.group(1).strip() if m else None
