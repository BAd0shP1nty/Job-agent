"""Mandatory skill-match condition.

A job is only eligible when at least one *confirmed* candidate skill (from the
reviewed resume profile or ``list.py``) literally appears in the listing - either
verbatim or through a small, documented alias table of exact synonyms. Loose
associations (e.g. "cloud" for "AWS") are deliberately not treated as matches.
"""
from __future__ import annotations

import re

from database.models import EvidenceSpan, SkillMatch
from screening.text_utils import split_sentences

# Exact synonyms / expansions only. Keys are compared case-insensitively.
SKILL_ALIASES: dict[str, list[str]] = {
    "ai": ["artificial intelligence"],
    "generative ai": ["genai", "gen ai", "gen-ai", "generative artificial intelligence"],
    "agentic ai": ["agentic", "ai agents", "ai agent"],
    "machine learning": ["ML"],
    "itsm": ["it service management"],
    "sre": ["site reliability engineering", "site reliability engineer"],
    "vibe coding": ["vibe-coding"],
    "service management": ["it service management"],
    "project management": ["project manager", "programme management", "program management"],
    "service delivery": ["service delivery manager"],
    "llms": ["llm", "large language model", "large language models"],
    "llm": ["llms", "large language model", "large language models"],
    "rag": ["retrieval augmented generation", "retrieval-augmented generation"],
    "aws": ["amazon web services"],
    "ci/cd": ["continuous integration", "continuous delivery"],
    "safe": ["scaled agile framework", "scaled agile"],
    "oss/bss": ["oss", "bss"],
    "prompt engineering": ["prompt engineer"],
    "incident management": ["incident manager"],
    "change management": ["change governance"],
}

# Short all-caps acronyms are matched case-sensitively to avoid false hits
# (e.g. "SAFe" vs "safe", "ML" vs "html").
_CASE_SENSITIVE = {"AI", "ML", "SRE", "ITIL", "ITSM", "AWS", "RAG", "MCP", "LLM", "LLMs", "SAFe", "OSS", "BSS", "SAP"}


def _pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term).replace(r"\ ", r"[\s\-]+")
    flags = 0 if term in _CASE_SENSITIVE or (term.isupper() and len(term) <= 5) else re.IGNORECASE
    return re.compile(rf"(?<![\w/]){escaped}(?![\w/])", flags)


def terms_for_skill(skill: str) -> list[str]:
    return [skill] + SKILL_ALIASES.get(skill.casefold(), [])


def match_skills(skills: list[str], text: str, url: str | None = None) -> list[SkillMatch]:
    """Return confirmed skill matches with the sentence that proves each one."""
    sentences = split_sentences(text)
    matches: list[SkillMatch] = []
    for skill in skills:
        for term in terms_for_skill(skill):
            pattern = _pattern(term)
            hit = next((s for s in sentences if pattern.search(s.text)), None)
            if hit:
                matches.append(
                    SkillMatch(
                        skill=skill,
                        matched_term=term,
                        evidence=EvidenceSpan(
                            text=hit.text[:400], source="job_listing", start=hit.start, end=hit.end, url=url
                        ),
                    )
                )
                break
    return matches


def passes_skill_filter(matches: list[SkillMatch]) -> bool:
    return len(matches) >= 1


# Vocabulary used to spot requirements in a listing that the candidate has *not*
# confirmed ("missing or unverified required skills").
REQUIREMENT_VOCABULARY = [
    "Python", "Java", "Golang", "Kubernetes", "Docker", "Terraform", "Ansible", "AWS", "Azure", "GCP", "ServiceNow",
    "Jira", "ITIL", "ITSM", "SRE", "DevOps", "CI/CD", "SQL", "Linux", "Dynatrace", "Splunk", "Elasticsearch",
    "Prometheus", "Grafana", "Datadog", "PagerDuty", "Machine Learning", "Deep Learning", "NLP", "LLM", "RAG",
    "LangChain", "LangGraph", "Generative AI", "Agentic AI", "Prompt Engineering", "MLOps", "PyTorch",
    "TensorFlow", "Scrum", "Agile", "SAFe", "PMP", "PRINCE2", "Six Sigma", "Service Delivery",
    "Incident Management", "Problem Management", "Change Management", "Release Management", "Vendor Management",
    "Stakeholder Management", "Budget Management", "Telecom", "OSS/BSS", "Mediation", "Online Charging", "5G",
    "Salesforce", "SAP", "Power BI", "Tableau", "Copilot Studio", "Amazon Bedrock", "SageMaker", "n8n", "MCP",
]


def required_skills_in_listing(text: str) -> list[str]:
    found = []
    for term in REQUIREMENT_VOCABULARY:
        if any(_pattern(t).search(text) for t in terms_for_skill(term)):
            found.append(term)
    return found


def missing_skills(required: list[str], confirmed: list[str]) -> list[str]:
    confirmed_keys = set()
    for skill in confirmed:
        confirmed_keys.add(skill.casefold())
        confirmed_keys.update(a.casefold() for a in SKILL_ALIASES.get(skill.casefold(), []))
    return [r for r in required if r.casefold() not in confirmed_keys
            and not any(a.casefold() in confirmed_keys for a in SKILL_ALIASES.get(r.casefold(), []))]
