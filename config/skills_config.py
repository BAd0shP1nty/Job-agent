"""Load and save the root-level ``list.py`` skills configuration.

``list.py`` holds ``SKILLS = {...}`` where the right-hand side is valid JSON.
It is parsed as text (never imported or executed) so a malformed file cannot run
code, and so we can show a precise JSON error to the user.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from config.settings import SKILLS_FILE

_ASSIGNMENT = re.compile(r"^SKILLS\s*=\s*", re.MULTILINE)

HEADER = (
    "# Additional target skills for the Autopilot Job Hunt Agent.\n"
    "#\n"
    "# The value assigned to SKILLS must remain valid JSON (double-quoted strings,\n"
    "# no trailing commas). The application parses this file as JSON text rather\n"
    "# than importing it, and rewrites it when skills are edited in the GUI.\n"
)


class SkillsConfigError(ValueError):
    """Raised when list.py cannot be parsed or has the wrong structure."""


def _dedupe(skills: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for skill in skills:
        cleaned = " ".join(str(skill).split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def load_skills(path: Path | None = None) -> list[str]:
    path = path or SKILLS_FILE
    if not path.exists():
        raise SkillsConfigError(f"Skills file not found: {path.name}")
    text = path.read_text(encoding="utf-8")
    match = _ASSIGNMENT.search(text)
    if not match:
        raise SkillsConfigError("list.py must contain an assignment of the form `SKILLS = {...}`.")
    payload = text[match.end():].strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SkillsConfigError(
            f"list.py is not valid JSON after `SKILLS =` (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise SkillsConfigError('SKILLS must be a JSON object with a "skills" array.')
    if not all(isinstance(s, str) for s in data["skills"]):
        raise SkillsConfigError('Every entry in "skills" must be a string.')
    return _dedupe(data["skills"])


def save_skills(skills: list[str], path: Path | None = None) -> list[str]:
    path = path or SKILLS_FILE
    cleaned = _dedupe(skills)
    body = json.dumps({"skills": cleaned}, indent=4, ensure_ascii=False)
    tmp = path.with_suffix(".py.tmp")
    tmp.write_text(f"{HEADER}SKILLS = {body}\n", encoding="utf-8")
    tmp.replace(path)
    load_skills(path)  # round-trip validation
    return cleaned


def merge_skills(resume_skills: list[str], configured_skills: list[str]) -> list[str]:
    """Merge resume + configured skills without case-insensitive duplicates."""
    return _dedupe(list(resume_skills) + list(configured_skills))
