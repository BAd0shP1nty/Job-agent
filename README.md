# 🧭 Autopilot Job Hunt Agent

A local-first, GUI-based job-search agent. It finds openings from permitted sources, checks each one
against your resume and a configurable skills list with strict, deterministic rules, explains each
match using quoted evidence, and puts only eligible roles into a human approval dashboard.

**Stack:** Python 3.10+ · LangGraph · Streamlit · SQLite · Claude Sonnet (Anthropic API, optional) ·
PyMuPDF / python-docx · local hybrid retrieval (ChromaDB optional)

> **What it will never do:** invent listings, skills, sponsorship or company details; scrape sites
> that prohibit it; bypass logins, CAPTCHAs or rate limits; or submit applications. When a fact can't
> be verified, it is marked unknown and the job stays out of any step that needs verified information.

---

## 1. Quick start

```bash
# 1. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure (optional: add your Anthropic API key for Claude explanations)
cp .env.example .env               # Windows: copy .env.example .env
#   then edit .env → ANTHROPIC_API_KEY=sk-ant-...

# 3. Launch the GUI
streamlit run app.py               # opens http://localhost:8501
```

**First use**

1. **Resume & Skills** → upload your resume (PDF/DOCX/TXT/MD) → review and correct the extracted
   profile → **Save as new resume version**.
2. **Sources & Connectors** → enable the sources you want. Add employer board tokens for
   Greenhouse/Lever/Ashby, or career-page URLs. Press **Test connection**.
3. **Find Jobs** → choose options → **🚀 Run Job Search**.
4. **Pending Approval** → **⭐ Mark Relevant** or **🚫 Ignore**.
5. **Selected Jobs** → set **Status** to *Applied* after you have applied yourself.
6. **Applied Jobs** → records stay visible for 15 days from when the job was first discovered.

**Try the whole lifecycle on mock data (no network needed)**

```bash
python scripts/demo_lifecycle.py                          # fixtures only
python scripts/demo_lifecycle.py --resume path/to/cv.docx # also ground matches in your resume
```

To try it in the GUI, enable **TEST FIXTURES** in *Sources & Connectors*. Every fixture job
carries a pink **TEST FIXTURE – not a real vacancy** badge, uses a fictional `[TEST FIXTURE]`
employer, and links to a reserved `example.*` domain.

**Run the tests**

```bash
python -m pytest            # 151 tests, about 10 s, fully offline
```

---

## 2. Architecture

```
app.py                     Streamlit entry point + sidebar navigation (9 sections)
list.py                    Additional target skills (JSON value assigned to SKILLS)
config/                    settings (env vars), logging (with redaction), list.py loader/saver
frontend/                  one module per page + shared components/theme
agent/                     LangGraph state, nodes, routing, graph, prompts, Claude client
discovery/                 connector interface, polite HTTP client, connectors, registry
rag/                       resume parser, chunking, embeddings, vector store, retrieval, matcher
screening/                 skill, location and visa filters, deduplication, output validation
database/                  connection, migrations, models, repository, lifecycle service
tests/                     pytest suite + fixtures/mock_jobs.json (mock data only)
scripts/demo_lifecycle.py  end-to-end lifecycle demo
```

### LangGraph workflow (`agent/graph.py`)

```
START → load_profile → build_search_plan → discover_jobs → extract_details → apply_hard_rules
      → record_rejections ─┬─ nothing passed ──────────────▶ finalize
                           └─ deduplicate ─┬─ all known ─────▶ finalize
                                           └─ retrieve_evidence → match_analysis → validate_output
      validate_output ─┬─ invalid (1 retry) → retry_or_flag → match_analysis (deterministic fallback)
                       └─ save_pending → finalize → END
```

* Each node is a method of `AgentNodes`. Dependencies (database, registry, LLM callable) are
  injected, so every stage can be tested on its own.
* Errors are caught for each node, logged to `agent_logs` and stored in the state. A failure in
  one source never stops the others.
* **Human approval is not a graph node.** The GUI calls the transactional
  `JobLifecycleService`. The database is the only source of truth for job status. The in-memory
  LangGraph checkpoint exists only for inspecting a run. On startup, any run left `running`
  after a crash or restart is marked `interrupted`. Because deduplication is persistent, re-running
  a search is always safe.

---

## 3. Screening rules (deterministic, enforced in Python)

The model never decides eligibility. It only sees jobs that have already passed these rules, and
its output can't change them.

| Rule | Behaviour |
|---|---|
| **Mandatory skill match** | At least one *confirmed* skill must appear in the listing: resume skills you reviewed plus `list.py` skills. A match is a literal occurrence or a documented exact synonym (`screening/skill_filter.py → SKILL_ALIASES`), and each one is stored with the sentence that proves it. Loose associations such as "cloud" for "AWS" don't count. Short acronyms are case-sensitive, so "AI" doesn't match "Mumbai". |
| **India** | Office, hybrid and remote roles are accepted in every Indian city. The optional **Bangalore-only** setting keeps only Bangalore roles and remote India roles. |
| **Outside India** | Accepted only with an explicit quote showing at least one of: **remote work open to someone in India** (India/APAC named, or "work from anywhere / worldwide", or a structured candidate-location field), **relocation assistance**, or **visa sponsorship**. "Remote" alone is not enough, and neither is "hybrid". |
| **Ambiguity** | Hedged wording ("may", "case-by-case"), contradictory statements and unknown locations become `requires_verification`. They are logged as *unverified* and kept out of the queue, never upgraded. |
| **Target markets** | Overseas roles outside India/EMEA are accepted only when remote work from India is verified. |
| **Evidence fields** | Each accepted overseas job stores `remote_eligibility`, `relocation_evidence`, `visa_sponsorship_evidence` and `evidence_source_url`. |

Every rejection is written to the internal search log with a short reason. You can see these in
**Agent Logs → Listing outcomes**.

## 4. Grounded matching (RAG)

* **Resume parsing** runs locally: PyMuPDF for PDF, python-docx for DOCX, and plain reading for
  TXT/MD. Sections keep their character offsets and page numbers. Skills are extracted only when
  they literally appear in the document, each with its source snippet. Nothing is inferred; you
  review and edit the profile before it's used.
* **Retrieval is hybrid:** cosine similarity from a configurable embedder, combined with exact
  skill and keyword overlap. The default `hashing` embedder is local, deterministic and needs no
  download. `sentence-transformers` is optional (`EMBEDDING_BACKEND`). The vector store is
  in-memory by default, which is enough for one resume. Set `USE_CHROMA=true` to persist it to
  `data/vector_store`.
* **Claude Sonnet** (`CLAUDE_MODEL`, default `claude-sonnet-5`) writes the explanation. It is
  called with a strict JSON schema (`output_config.format`). The output is then validated again
  in `screening/validation.py`:
  * the schema must hold;
  * every resume or listing quote must appear word for word in the passages supplied;
  * confirmed skills must be a subset of the deterministic matches;
  * the output must make no hiring-probability or "application submitted" claims.
  Unsupported items are dropped. If most evidence fails, or the JSON is malformed, the call gets
  **one bounded repair attempt**. If that also fails, the deterministic explanation is used and a
  note is shown on the card.
* **Untrusted content:** listing text is sanitized, then wrapped in `<untrusted_job_listing>`
  tags and passed as *retrieved passages only*, never the whole page. Instruction-like text is
  detected and flagged on the card, and it never changes behaviour.
* **Privacy:** by default only skill names and listing passages are sent to Claude. Resume
  excerpts are sent only if you turn on *Allow resume excerpts to be sent to Claude* in Search
  Settings, or set `ALLOW_RESUME_TO_LLM=true`. The full resume is never sent. Without an API key
  the app runs fully locally with deterministic explanations.

### Internal relevance score (not a hiring probability)

```
score = 100 × Σ wᵢ·cᵢ / Σ wᵢ      (weights editable in Search Settings)
  skill_coverage        = confirmed skills / (confirmed + listing requirements you haven't confirmed)
  title_alignment       = best token overlap between the job title and your target titles / headline
  evidence_completeness = share of: posting date verified, location determined, arrangement stated,
                          resume evidence found, ≥2 skills evidenced in the listing
  recency               = 1.0 if posted ≤7 days ago, falling linearly to 0 at 60 days; 0 if unknown
```

**Confidence** (high/medium/low) reflects evidence completeness only.

## 5. Lifecycle, deduplication and suppression

```
pending ──Mark Relevant──▶ selected ──Status: Applied──▶ applied ──15 days after ingestion──▶ hidden, then purged
   └──Ignore──▶ active record deleted; only a minimal fingerprint is kept
```

* Each transition runs in one `BEGIN IMMEDIATE` transaction and checks the job's current status,
  so a job is never in two states and a double click can't apply a change twice.
* A **suppression fingerprint** is written as soon as a job leaves the pending queue (selected,
  ignored, applied, expired). The ledger holds only hashes, a source name, a timestamp and a
  reason. No title, description or URL text is kept.
* **Duplicate detection** checks, in order:
  1. the canonical URL hash (tracking parameters, `www`, fragments and regional LinkedIn hosts are
     stripped);
  2. a strict fingerprint (normalized company + title + city + country + requisition id);
  3. a loose key without the requisition id, which catches the same job posted on another portal.
     Two listings with *different explicit requisition ids* are treated as different jobs.
  A job found on several portals becomes one card with every source URL listed. Very similar
  titles at the same employer and location go to a **Possible duplicates** review section instead
  of being merged or deleted.
* **Applied expiry** is 15 days from the original `ingested_at` timestamp, not from when you
  marked it applied. The view hides expired rows with a query. On each app load, the stored
  description of expired jobs is deleted, but the fingerprint stays, so an expired job never
  comes back as a new opportunity.
* Changing search settings never deletes selected or applied jobs.

## 6. Job sources: status

A connector only shows **working** in the GUI after a real successful call. Here is the current
state of each one:

| Source | Type | Implementation | Access requirement |
|---|---|---|---|
| Remotive | Official public API | ✅ implemented, tested against the documented response shape | None. Terms ask for a link back and low request volume (cached 6 h). |
| Arbeitnow | Official public API (DE/EU) | ✅ implemented, tested against the documented shape | None |
| Himalayas | Official public API (remote) | ✅ implemented, tested against the documented shape | None |
| Adzuna | Official API (IN, UK, EU) | ✅ implemented, tested against the documented shape | Free `ADZUNA_APP_ID` / `ADZUNA_APP_KEY`. Descriptions are snippets, so overseas jobs often stay unverified. |
| Greenhouse Job Board API | Employer career sites | ✅ implemented, tested against the documented shape | Board tokens of employers you choose |
| Lever Postings API | Employer career sites | ✅ implemented, tested against the documented shape | Company slugs (`region=eu` for EU boards) |
| Ashby Job Posting API | Employer career sites | ✅ implemented, tested against the documented shape | Board names |
| Career pages (schema.org `JobPosting`) | Public pages | ✅ implemented, respects robots.txt | Page URLs you add. Pages without JobPosting JSON-LD are skipped. |
| LinkedIn Jobs | — | ⛔ registered, cannot be enabled | No public job-search API; needs an approved LinkedIn partner integration. Scraping is prohibited. |
| Naukri | — | ⛔ registered, cannot be enabled | No public API; needs a commercial/recruiter agreement. |
| Indeed | — | ⛔ registered, cannot be enabled | Publisher API closed to new integrations; needs a partner programme. |
| Test fixtures | Local mock file | ✅ working | Mock data for demos and tests only |

> **Live-verification status:** the development container's network policy blocked every
> job-board host (HTTP 403 at the proxy), so no connector has been confirmed against a live
> endpoint yet. They are tested against the providers' documented response shapes
> (`tests/test_connectors.py`). The first time you run **Test connection** on your own machine,
> each connector's real status is recorded. If a provider has changed its schema, the connector
> reports `failing` with the error rather than producing guessed data.

All HTTP goes through `discovery/base.HttpClient`, which provides:

* per-host rate limiting;
* up to 3 retries with exponential backoff that honours `Retry-After`;
* SQLite response caching;
* robots.txt checks for HTML pages;
* reporting 401/403 and proxy refusals as `blocked`, never retried or bypassed.

**Adding a source:** subclass `SourceAdapter`, set `info = ConnectorInfo(...)`, implement
`search()` to return `RawListing`s, and add the class to `CONNECTOR_CLASSES` in
`discovery/registry.py`. The graph doesn't change.

## 7. Security and privacy

* API keys are read from environment variables or `.env` only. `.env`, resumes (`*.pdf`,
  `*.docx`), databases and `data/` are git-ignored.
* Uploads are checked for extension allow-list, 5 MB limit, file signature, and DOCX zip
  structure. Macro-enabled or zip-bomb-sized DOCX files are rejected. Only extracted text is
  stored, in the local SQLite database. `list.py` is parsed as JSON text and never imported or
  executed.
* Logs never include resume text or descriptions, and e-mail addresses and phone numbers are
  redacted. External text is HTML-escaped before rendering, and only `http(s)` links are shown.
* **Data retention:** delete resume versions in *Resume & Skills*. *Search Settings → Data
  retention* deletes all job data. Keeping suppression fingerprints there is optional.

## 8. Configuration reference (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Enables Claude explanations |
| `CLAUDE_MODEL` | `claude-sonnet-5` | Claude model id |
| `ALLOW_RESUME_TO_LLM` | `false` | Default for sending resume excerpts (GUI toggle overrides) |
| `JOBHUNT_DB_PATH` | `data/jobhunt.db` | SQLite database |
| `EMBEDDING_BACKEND` | `hashing` | `hashing` or `sentence-transformers` |
| `USE_CHROMA` | `false` | Persist vectors with ChromaDB |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | – | Adzuna credentials |

**PostgreSQL later:** all SQL lives in `database/repository.py` and `database/lifecycle.py`, and
connections come from `database/connection.Database` (`connect()` / `transaction()`). To switch,
provide a Postgres implementation of that class and change `?` placeholders to `%s`.

## 9. Test coverage

| File | What it covers |
|---|---|
| `test_resume_parser.py` | DOCX/PDF/TXT parsing, reviewable profile, literal-only skills, merging with `list.py`, unsafe uploads |
| `test_skill_filter.py` | `list.py` load/save/validation errors, evidence-backed matches, no vague matches, acronyms |
| `test_location_filter.py` | Bangalore and other Indian cities, Bangalore-only, remote-only, unknown locations, country detection |
| `test_visa_filter.py` | Sponsorship, relocation and remote-from-India evidence; hedged/negative/contradictory wording never upgraded |
| `test_deduplication.py` | Tracking URLs, normalization, cross-portal merge, requisition ids, suppression, fuzzy review, minimal ledger |
| `test_job_lifecycle.py` | Every transition, 15-day expiry from ingestion, cleanup, invalid transitions, rollback, restart recovery |
| `test_agent_graph.py` | Full graph on fixtures, repeated runs, no reappearance, source isolation, malformed/ungrounded LLM output, privacy |
| `test_connectors.py` | Each connector's parsing, robots.txt, 403/proxy handling, bounded backoff, rate limiting, cache |
| `test_llm_client.py` | Structured-output request, refusal/truncation, network errors, untrusted wrapping |
| `test_ui.py` | Every navigation button, empty states, DB-driven counts, GUI search run, approval buttons, Applied dropdown, HTML escaping |

The fixture listings in `tests/fixtures/mock_jobs.json` are fictional and each one states its
test purpose.
