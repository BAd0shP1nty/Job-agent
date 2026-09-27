"""Save API keys entered in the GUI to the local .env file (never to the database or logs).

Only an allow-listed set of variable names can be written. The value is also put
into the running process environment so it takes effect without a restart.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from config.settings import PROJECT_ROOT, get_settings, reset_settings

ENV_FILE = PROJECT_ROOT / ".env"
ALLOWED_SECRETS = {
    "ANTHROPIC_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
    "JOOBLE_API_KEY_IN", "JOOBLE_API_KEY_UK", "JOOBLE_API_KEY_IE", "JOOBLE_API_KEY_DE", "JOOBLE_API_KEY_FR",
    "JOOBLE_API_KEY_NL", "JOOBLE_API_KEY_AE", "JOOBLE_API_KEY_US",
}


class SecretError(ValueError):
    pass


def mask(value: str | None) -> str:
    if not value:
        return "not set"
    return f"saved (…{value[-4:]})" if len(value) > 6 else "saved"


def current(name: str) -> str | None:
    return os.getenv(name) or None


def save_secret(name: str, value: str, env_file: Path | None = None) -> None:
    if name not in ALLOWED_SECRETS:
        raise SecretError(f"{name} is not a recognised key name.")
    value = (value or "").strip().strip('"').strip("'")
    if not value:
        raise SecretError("The key is empty.")
    if re.search(r"[\s#=]", value):
        raise SecretError("That doesn't look like a key (it contains spaces, '#' or '=').")
    env_file = env_file or ENV_FILE
    if not env_file.exists():
        example = env_file.parent / ".env.example"
        if example.exists():
            shutil.copy(example, env_file)
        else:
            env_file.write_text("", encoding="utf-8")
    lines = env_file.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{name}={value}"
            replaced = True
    if not replaced:
        lines.append(f"{name}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[name] = value
    # Rebuild settings from the environment so the new key is used immediately.
    old = get_settings()
    reset_settings(db_path=old.db_path, data_dir=old.data_dir)
