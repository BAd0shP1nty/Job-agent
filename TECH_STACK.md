# Autopilot Job Hunt Agent — What We Built & Tech Stack

## What it is
A desktop web app (runs locally in your browser) that:
1. **Reads your resume** (PDF/DOCX/TXT/MD) and extracts only the skills, roles and certifications actually written in it. You review and correct them.
2. **Searches job sources** (official APIs and employer career sites) for India + EMEA roles.
3. **Screens every job with strict rules written in code:**
   - The job must mention at least one of your confirmed skills.
   - India roles are accepted (office, hybrid or remote).
   - Overseas roles are accepted **only** if the listing explicitly offers remote-from-India, relocation, or visa sponsorship.
   - Anything ambiguous is held back, never assumed.
4. **Explains each match with quoted evidence** from your resume and the job listing, plus an internal relevance score (not a hiring probability).
5. **Lets you approve jobs:** Pending → *Mark Relevant* / *Ignore* → Selected → *Applied*. Applied jobs stay visible for 15 days, and processed jobs never reappear.

It never invents jobs, never scrapes sites that forbid it, and never submits applications.

---

## Technology choices at a glance

| Layer | What we used | Why |
|---|---|---|
| **Agent code developer (built with)** | **Claude Code** (Anthropic's AI coding agent, cloud session) | Wrote, tested and pushed the full codebase. Not used: Antigravity, Replit, Copilot. |
| **Programming language** | **Python 3.10+** | One language for UI, agent, RAG and database |
| **Frontend / UI** | **Streamlit** + custom CSS theme (indigo/cyan palette, status badges, job cards) | Fastest way to build a polished, interactive Python GUI. The page code is separate from the rest of the app, so it can be replaced by a React front end later. |
| **Backend** | Plain Python modules: `agent/`, `screening/`, `rag/`, `discovery/`, `database/` | No separate server needed. The GUI calls the backend services directly. |
| **Agentic framework** | **LangGraph** (13-node `StateGraph` with conditional routing, retry loop, per-node error handling) | Clear, testable pipeline steps. LangChain itself is *not* used directly (only `langchain-core`, which LangGraph installs as a dependency). |
| **LLM for reasoning** | **Claude Sonnet** (`claude-sonnet-5`) via the official **Anthropic Python SDK**, with JSON-schema structured output | Writes the match explanation. It **cannot** override the eligibility rules, and every quote it gives is checked word-for-word against the source text. Optional: the app works without an API key. |
| **Embedding model** | Default: **local hashing embedder** (custom, offline, no download). Optional: **sentence-transformers `all-MiniLM-L6-v2`** | Works offline and needs no model download; switch in `.env` |
| **Vector database** | Default: **in-memory vector store** (one resume = small data). Optional: **ChromaDB** (`USE_CHROMA=true`) | The brief said not to depend on a vector DB unless needed |
| **Retrieval (RAG)** | **Hybrid retrieval**: semantic similarity + exact skill/keyword matching, with section/page/offset metadata | Grounded evidence for every match |
| **Database** | **SQLite** with versioned migrations and a repository layer | Local and simple. Status changes are atomic transactions, and the design is ready for PostgreSQL later. |
| **Resume parsing** | **PyMuPDF** (PDF), **python-docx** (DOCX) | Local, no cloud upload |
| **Data validation** | **Pydantic v2** | Strict schemas for listings, profiles and LLM output |
| **Job sources (APIs)** | **Remotive, Arbeitnow, Himalayas** (public APIs), **Adzuna** and **Jooble** (free keys, good for India), **"Add a job you found"** (paste from LinkedIn/Naukri), **Greenhouse / Lever / Ashby** (employer career-site APIs) | Official, permitted access only |
| **Web scraping** | **No scraping of job portals.** Only reads schema.org `JobPosting` data on career pages you list, if the site's `robots.txt` allows it. Uses **requests** + **BeautifulSoup**. | LinkedIn, Naukri and Indeed are listed but disabled: they need partner agreements and forbid scraping. |
| **HTTP reliability** | Custom client: rate limiting, retries with exponential backoff, caching, `robots.txt` checks | One failing source never stops the others |
| **MCP server** | **None used by the app.** | MCP can be added later as an optional connector. |
| **Testing** | **pytest** (165 tests) + **Streamlit AppTest** (GUI tests) + mock job fixtures | Covers rules, deduplication, lifecycle, agent graph, connectors and UI |
| **Config & secrets** | `.env` file via **python-dotenv**; skills in `list.py` (JSON) | No keys in code; resumes and databases are git-ignored |
| **Version control** | **Git / GitHub** — branch `claude/autopilot-job-hunt-agent-lkm0r9` | |

---

## How the agent flows (LangGraph)

```
Load profile → Build search plan → Discover jobs → Extract details
   → Apply hard rules (skills / location / visa) → Record rejections
   → Deduplicate → Retrieve resume evidence (RAG) → Claude match analysis
   → Validate evidence (retry once, else fall back) → Save to Pending queue
                              ↓
          You decide in the GUI: Mark Relevant / Ignore / Applied
```

## GUI sections
Dashboard · Find Jobs · Pending Approval · Selected Jobs · Applied Jobs · Resume & Skills · Search Settings · Sources & Connectors · Agent Logs

## Key design principles
- **Rules in code, reasoning by LLM:** the model explains; Python decides eligibility.
- **Evidence or nothing:** every skill and every visa/relocation claim carries a quote and a source URL.
- **Privacy first:** the resume is processed locally, and resume text goes to Claude only if you switch it on.
- **Honest connectors:** a source shows "working" only after a real successful call.

## How to run
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```
Full details are in `README.md`.
