"""End-to-end graph tests using mock listings (tests/fixtures/mock_jobs.json) and a fake LLM."""
import json
import re

import pytest

from agent.graph import build_graph, run_search
from agent.nodes import AgentDeps
from database.lifecycle import JobLifecycleService
from database.models import RawListing
from database.repository import JobRepository, ProfileRepository, RunRepository, SourceRegistryRepository
from discovery.base import ConnectorInfo, SourceAccessDenied, SourceAdapter
from discovery.registry import SourceRegistry
from rag.resume_parser import extract_profile, parse_resume


class ExplodingConnector(SourceAdapter):
    info = ConnectorInfo(name="exploding", display_name="Exploding", kind="official_api", description="",
                         access_notes="test only")

    def search(self, query):
        raise ValueError("unexpected payload")


class BlockedConnector(SourceAdapter):
    info = ConnectorInfo(name="blocked", display_name="Blocked", kind="official_api", description="",
                         access_notes="test only")

    def search(self, query):
        raise SourceAccessDenied("example.org denied access (HTTP 403).")


def make_deps(db, llm_call=None, extra=(), enabled=("test_fixtures",), skills_path=None):
    registry = SourceRegistry(db, extra_connectors=list(extra))
    repo = SourceRegistryRepository(db)
    for row in repo.list():
        repo.set_enabled(row["source_name"], row["source_name"] in enabled)
    return AgentDeps(db=db, registry=registry, llm_call=llm_call, skills_path=skills_path)


def add_resume(db, data):
    parsed = parse_resume("resume.docx", data)
    ProfileRepository(db).add("resume.docx", extract_profile(parsed), parsed.text)


def _passages(prompt: str, tag: str) -> list[str]:
    return [m.group(1) for m in re.finditer(rf"^\[{tag}\d+\] (.+)$", prompt, re.MULTILINE)]


def good_llm(messages):
    prompt = messages[0]["content"]
    jobs, resume = _passages(prompt, "J"), _passages(prompt, "R")
    skills = re.search(r"CONFIRMED SKILLS[^\n]*\n(.+)", prompt).group(1).split(", ")
    return json.dumps({
        "summary": "The listing asks for skills the candidate has used, supported by the quoted evidence.",
        "relevant_experience": [{"claim": "Relevant operations work", "resume_quote": resume[0][:40]}] if resume else [],
        "job_requirements": [{"requirement": "Core requirement", "listing_quote": jobs[0][:40]}],
        "confirmed_skills": skills[:2],
        "unverified_requirements": [],
        "concerns": [],
    })


def test_graph_has_expected_nodes(db):
    graph = build_graph(make_deps(db))
    nodes = set(graph.get_graph().nodes)
    assert {"load_profile", "build_search_plan", "discover_jobs", "extract_details", "apply_hard_rules",
            "record_rejections", "deduplicate", "retrieve_evidence", "match_analysis", "validate_output",
            "retry_or_flag", "save_pending", "finalize"} <= nodes


def test_full_run_with_fixtures(db, sample_resume_bytes):
    add_resume(db, sample_resume_bytes)
    final = run_search(make_deps(db))
    repo = JobRepository(db)
    pending = {j["title"]: j for j in repo.list_by_status("pending")}
    review = repo.list_by_status("duplicate_review")

    assert set(pending) == {"Service Delivery Manager - ITSM", "AI Program Manager", "Site Reliability Engineering Lead",
                            "GenAI Solutions Lead (Remote)", "SRE Manager", "Service Operations Manager"}
    assert [j["title"] for j in review] == ["AI Programme Manager"]
    # Cross-portal duplicate collapsed into one card with both source URLs.
    assert len(pending["Service Delivery Manager - ITSM"]["sources"]) == 2
    # Every queued job: test fixture badge, >=1 confirmed skill with evidence, direct URL.
    for job in list(pending.values()) + review:
        assert job["is_test_fixture"] is True
        assert job["matched_skills"]
        assert job["source_url"].startswith("https://")
        assert job["match_details"]["job_evidence"]
    # Overseas jobs carry evidence fields.
    assert pending["Site Reliability Engineering Lead"]["visa_sponsorship_evidence"]
    assert pending["Service Operations Manager"]["relocation_evidence"]
    assert "India" in pending["GenAI Solutions Lead (Remote)"]["remote_eligibility"]
    # Prompt-injection text is flagged, never obeyed.
    notes = " ".join(pending["SRE Manager"]["match_details"]["validation_notes"])
    assert "instruction-like text" in notes

    log = {e["title"]: e for e in RunRepository(db).search_log(final["run_id"])}
    assert log["IT Service Manager"]["outcome"] == "rejected"
    assert log["Remote Service Management Consultant"]["outcome"] == "rejected"
    assert log["Service Delivery Lead"]["outcome"] == "unverified"
    assert log["Senior Java Backend Developer"]["reason"].startswith("no confirmed skill")
    assert log["ITSM Process Owner"]["outcome"] == "rejected"
    run = RunRepository(db).list()[0]
    assert run["status"] == "completed" and run["jobs_discovered"] == 13 and run["jobs_eligible"] == 7


def test_repeated_searches_do_not_create_duplicates(db, sample_resume_bytes):
    add_resume(db, sample_resume_bytes)
    deps = make_deps(db)
    run_search(deps)
    with db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    final = run_search(deps)
    with db.connect() as conn:
        after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert before == after == 7
    assert final.get("saved_job_ids", []) == []


def test_processed_jobs_never_reappear(db, sample_resume_bytes):
    add_resume(db, sample_resume_bytes)
    deps = make_deps(db)
    run_search(deps)
    repo, svc = JobRepository(db), JobLifecycleService(db)
    by_title = {j["title"]: j["job_id"] for j in repo.list_by_status("pending")}
    svc.mark_relevant(by_title["AI Program Manager"])
    svc.ignore(by_title["SRE Manager"])
    svc.mark_relevant(by_title["Service Delivery Manager - ITSM"])
    svc.mark_applied(by_title["Service Delivery Manager - ITSM"])

    run_search(deps)
    titles = [j["title"] for j in repo.list_by_status("pending")]
    assert "AI Program Manager" not in titles and "SRE Manager" not in titles
    assert "Service Delivery Manager - ITSM" not in titles
    assert len(repo.list_selected()) == 1 and len(repo.list_applied_visible()) == 1


def test_one_failing_source_does_not_stop_others(db, sample_resume_bytes):
    add_resume(db, sample_resume_bytes)
    deps = make_deps(db, extra=[ExplodingConnector, BlockedConnector],
                     enabled=("exploding", "blocked", "test_fixtures"))
    final = run_search(deps)
    assert len(final["saved_job_ids"]) == 7
    statuses = {r["source"]: r["status"] for r in final["source_results"]}
    assert statuses == {"exploding": "failing", "blocked": "blocked", "test_fixtures": "working"}
    reg = {r["source_name"]: r for r in SourceRegistryRepository(db).list()}
    assert reg["blocked"]["status"] == "blocked" and "403" in reg["blocked"]["last_error"]
    assert reg["test_fixtures"]["last_success_at"]
    assert RunRepository(db).list()[0]["status"] == "completed_with_errors"


def test_restricted_portals_never_run(db):
    deps = make_deps(db, enabled=("linkedin", "naukri", "indeed"))
    final = run_search(deps)
    assert final.get("raw_listings") == []
    assert {r["status"] for r in final["source_results"]} == {"requires_authorized_access"}


def test_valid_llm_output_is_used(db, sample_resume_bytes):
    add_resume(db, sample_resume_bytes)
    run_search(make_deps(db, llm_call=good_llm))
    jobs = JobRepository(db).list_by_status("pending")
    assert jobs and all(j["match_details"]["llm_used"] for j in jobs)
    assert jobs[0]["match_summary"].startswith("The listing asks")


@pytest.mark.parametrize("bad_output", [
    "not json at all",
    json.dumps({"summary": "ok"}),  # schema violation
    json.dumps({"summary": "You have a 90% chance of being hired here.", "relevant_experience": [],
                "job_requirements": [], "confirmed_skills": [], "unverified_requirements": [], "concerns": []}),
    json.dumps({"summary": "Fabricated evidence here for sure.", "relevant_experience": [
        {"claim": "x", "resume_quote": "Led a team of 500 at a Fortune 10 company"}],
        "job_requirements": [{"requirement": "y", "listing_quote": "requires 15 years of Kubernetes"}],
        "confirmed_skills": [], "unverified_requirements": [], "concerns": []}),
])
def test_malformed_or_ungrounded_llm_output_is_handled(db, sample_resume_bytes, bad_output):
    add_resume(db, sample_resume_bytes)
    calls = []

    def bad_llm(messages):
        calls.append(messages)
        return bad_output

    run_search(make_deps(db, llm_call=bad_llm))
    jobs = JobRepository(db).list_by_status("pending")
    assert len(jobs) == 6  # pipeline continues
    for job in jobs:
        details = job["match_details"]
        assert details["llm_used"] is False
        assert any("rejected after 2 attempt" in n for n in details["validation_notes"])
    assert len(calls) == 2 * 7  # one bounded repair attempt per job


def test_llm_cannot_add_unconfirmed_skills(db, sample_resume_bytes):
    add_resume(db, sample_resume_bytes)

    def sneaky_llm(messages):
        data = json.loads(good_llm(messages))
        data["confirmed_skills"] = data["confirmed_skills"] + ["Kubernetes", "Quantum Computing"]
        return json.dumps(data)

    run_search(make_deps(db, llm_call=sneaky_llm))
    for job in JobRepository(db).list_by_status("pending"):
        assert "Kubernetes" not in job["matched_skills"]
        assert any("skill not in confirmed list" in n for n in job["match_details"]["validation_notes"])


def test_llm_never_sees_rejected_jobs(db, sample_resume_bytes):
    add_resume(db, sample_resume_bytes)
    seen_titles = []

    def recording_llm(messages):
        seen_titles.append(messages[0]["content"].split("\n")[0])
        return good_llm(messages)

    run_search(make_deps(db, llm_call=recording_llm))
    joined = " ".join(seen_titles)
    for rejected in ("IT Service Manager", "Senior Java Backend Developer", "Service Delivery Lead",
                     "Remote Service Management Consultant", "ITSM Process Owner"):
        assert f"JOB: {rejected} at" not in joined


def test_resume_text_withheld_from_llm_by_default(db, sample_resume_bytes):
    add_resume(db, sample_resume_bytes)
    prompts = []

    def recording_llm(messages):
        prompts.append(messages[0]["content"])
        return good_llm(messages)

    run_search(make_deps(db, llm_call=recording_llm))
    assert prompts and all("[R1]" not in p for p in prompts)
    assert all("Resume text is not shared" in p for p in prompts)


def test_llm_outage_falls_back(db, sample_resume_bytes):
    add_resume(db, sample_resume_bytes)

    def down(messages):
        raise RuntimeError("connection reset")

    final = run_search(make_deps(db, llm_call=down))
    assert len(final["saved_job_ids"]) == 7


def test_runs_without_resume_using_list_py_skills(db):
    final = run_search(make_deps(db))
    assert final["resume_available"] is False
    assert final.get("saved_job_ids")
    for job in JobRepository(db).list_by_status("pending"):
        assert job["match_details"]["candidate_evidence"] == []


def test_broken_skills_config_fails_gracefully(db, tmp_path):
    bad = tmp_path / "list.py"
    bad.write_text("SKILLS = {not json}")
    final = run_search(make_deps(db, skills_path=bad))
    assert final["fatal"] is True
    assert any("Skills configuration error" in e for e in final["errors"])
    assert RunRepository(db).list()[0]["status"] == "failed"


def test_old_listings_are_filtered_by_date(db, monkeypatch):
    from database.repository import SettingsRepository

    SettingsRepository(db).save_search_settings({"date_posted_days": 3})
    final = run_search(make_deps(db))
    reasons = [r["reason"] for r in final["rejected"]]
    assert any("posted more than 3 days ago" in r for r in reasons)


def test_raw_listing_marks_fixture():
    from discovery.public_job_boards import load_fixture_listings

    listings = load_fixture_listings()
    assert all(isinstance(l, RawListing) and l.is_test_fixture for l in listings)
    assert all("example." in l.source_url for l in listings)
    assert all(l.company.startswith("[TEST FIXTURE]") for l in listings)
