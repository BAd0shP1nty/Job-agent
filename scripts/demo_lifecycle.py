"""Demonstrate the complete job lifecycle using TEST FIXTURES (mock listings, not real vacancies).

Usage:
    python scripts/demo_lifecycle.py [--resume path/to/resume.docx] [--db path/to/demo.db]

Uses a throwaway database (default: a temporary file) so your real data is untouched,
and enables only the test-fixture source, so no network access is needed.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.graph import run_search  # noqa: E402
from agent.nodes import AgentDeps  # noqa: E402
from config.settings import utcnow  # noqa: E402
from database.connection import Database  # noqa: E402
from database.lifecycle import JobLifecycleService  # noqa: E402
from database.migrations import migrate  # noqa: E402
from database.repository import JobRepository, ProfileRepository, RunRepository, SourceRegistryRepository  # noqa: E402
from discovery.registry import SourceRegistry  # noqa: E402
from rag.resume_parser import extract_profile, parse_resume  # noqa: E402


def banner(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", help="optional resume file (PDF/DOCX/TXT) used for grounded matching")
    parser.add_argument("--db", help="database path (default: temporary file)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else Path(tempfile.mkdtemp()) / "demo.db"
    db = Database(db_path)
    migrate(db)
    registry = SourceRegistry(db)
    sources = SourceRegistryRepository(db)
    for row in sources.list():
        sources.set_enabled(row["source_name"], row["source_name"] == "test_fixtures")
    print(f"Demo database: {db_path}\nSources enabled: test_fixtures only (mock data – NOT real vacancies)")

    if args.resume:
        data = Path(args.resume).read_bytes()
        parsed = parse_resume(Path(args.resume).name, data)
        profile = extract_profile(parsed)
        ProfileRepository(db).add(parsed.filename, profile, parsed.text)
        print(f"Resume parsed locally: {len(profile.skills)} skills, {len(profile.experience)} roles.")

    deps = AgentDeps(db=db, registry=registry, llm_call=None)
    repo, svc, runs = JobRepository(db), JobLifecycleService(db), RunRepository(db)

    banner("1. First search run")
    final = run_search(deps)
    for r in runs.search_log(final["run_id"])[::-1]:
        print(f"  [{r['outcome']:<16}] {r['title'][:45]:<45} {r['reason'][:70]}")
    pending = repo.list_by_status("pending")
    print(f"\nPending approval: {len(pending)} · duplicate review: {len(repo.list_by_status('duplicate_review'))}")
    for j in pending:
        print(f"  • {j['title']} | {j['location']} | score {j['match_score']} ({j['confidence']}) | "
              f"skills: {', '.join(j['matched_skills'][:5])} | sources: {len(j['sources'])}")

    by_title = {j["title"]: j for j in pending}
    banner("2. Human approval: Mark Relevant x2, Ignore x1")
    svc.mark_relevant(by_title["AI Program Manager"]["job_id"])
    svc.mark_relevant(by_title["Service Delivery Manager - ITSM"]["job_id"])
    svc.ignore(by_title["SRE Manager"]["job_id"])
    print("Selected:", [j["title"] for j in repo.list_selected()])
    print("Pending now:", [j["title"] for j in repo.list_by_status("pending")])
    for j in repo.list_selected():
        print(f"  link → {j['source_url']}")

    banner("3. Selected → Applied (status dropdown)")
    expires = svc.mark_applied(by_title["Service Delivery Manager - ITSM"]["job_id"])
    print("Selected:", [j["title"] for j in repo.list_selected()])
    applied = repo.list_applied_visible()
    print("Applied (visible):", [(a["title"], a["ingested_at"], a["expires_at"]) for a in applied])
    print(f"Expiry = ingestion + 15 days = {expires}")

    banner("4. Second search run – processed jobs must not reappear")
    final2 = run_search(deps)
    print(f"New jobs saved: {len(final2.get('saved_job_ids', []))} · duplicates skipped: {final2.get('duplicates')}")
    titles = [j["title"] for j in repo.list_by_status("pending")]
    for t in ("AI Program Manager", "Service Delivery Manager - ITSM", "SRE Manager"):
        print(f"  {t!r} in pending queue? {t in titles}")

    banner("5. 15-day expiry of applied jobs (simulated clock +16 days)")
    future = (utcnow() + timedelta(days=16)).isoformat(timespec="seconds")
    print("Visible applied records at +16 days:", len(repo.list_applied_visible(now_iso=future)))
    print("Cleanup removed:", svc.cleanup_expired_applied(now_iso=future), "record(s); fingerprint retained.")
    final3 = run_search(deps)
    print("Third run – new jobs saved:", len(final3.get("saved_job_ids", [])),
          "(expired applied job stays suppressed)")

    banner("Summary")
    print(repo.counts())


if __name__ == "__main__":
    main()
