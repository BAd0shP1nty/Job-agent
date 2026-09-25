"""Application settings loaded from environment variables (and an optional .env file).

Secrets (API keys) are only ever read from the environment - never hardcoded.
User-facing search preferences are persisted in the database instead
(see ``database.repository.SettingsRepository``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:  # python-dotenv is optional at runtime
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_FILE = PROJECT_ROOT / "list.py"

APPLIED_VISIBILITY_DAYS = 15
MAX_RESUME_BYTES = 5 * 1024 * 1024
ALLOWED_RESUME_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    claude_model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-5"))
    allow_resume_to_llm: bool = field(default_factory=lambda: _bool_env("ALLOW_RESUME_TO_LLM", False))
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / os.getenv("JOBHUNT_DATA_DIR", "data"))
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / os.getenv("JOBHUNT_DB_PATH", "data/jobhunt.db"))
    embedding_backend: str = field(default_factory=lambda: os.getenv("EMBEDDING_BACKEND", "hashing"))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    use_chroma: bool = field(default_factory=lambda: _bool_env("USE_CHROMA", False))
    adzuna_app_id: str | None = field(default_factory=lambda: os.getenv("ADZUNA_APP_ID") or None)
    adzuna_app_key: str | None = field(default_factory=lambda: os.getenv("ADZUNA_APP_KEY") or None)
    http_user_agent: str = field(
        default_factory=lambda: os.getenv("HTTP_USER_AGENT", "AutopilotJobHunt/1.0 (personal job search assistant)")
    )
    http_timeout: float = field(default_factory=lambda: float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")))

    @property
    def llm_available(self) -> bool:
        return bool(self.anthropic_api_key)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(**overrides) -> Settings:
    """Rebuild settings (used by tests)."""
    global _settings
    _settings = Settings(**overrides)
    return _settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
