"""API keys entered in the GUI go to the local .env file only."""
import os

import pytest

import config.secrets as secrets
from config.settings import get_settings


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    (tmp_path / ".env.example").write_text("ANTHROPIC_API_KEY=\nADZUNA_APP_ID=\nADZUNA_APP_KEY=\n")
    monkeypatch.setattr(secrets, "ENV_FILE", path)
    for name in secrets.ALLOWED_SECRETS:
        monkeypatch.delenv(name, raising=False)
    yield path
    for name in secrets.ALLOWED_SECRETS:
        os.environ.pop(name, None)


def test_save_creates_env_from_example_and_applies_immediately(env_file):
    secrets.save_secret("ADZUNA_APP_ID", "  abc123  ")
    secrets.save_secret("ADZUNA_APP_KEY", "k" * 32)
    text = env_file.read_text()
    assert "ADZUNA_APP_ID=abc123" in text and f"ADZUNA_APP_KEY={'k' * 32}" in text
    assert "ANTHROPIC_API_KEY=" in text  # rest of the example kept
    assert get_settings().adzuna_app_id == "abc123"  # no restart needed


def test_save_replaces_existing_value(env_file):
    secrets.save_secret("JOOBLE_API_KEY_IN", "first-key")
    secrets.save_secret("JOOBLE_API_KEY_IN", "second-key")
    text = env_file.read_text()
    assert text.count("JOOBLE_API_KEY_IN=") == 1 and "second-key" in text


@pytest.mark.parametrize("name,value,msg", [
    ("PATH", "x", "not a recognised"),
    ("ADZUNA_APP_ID", "   ", "empty"),
    ("ADZUNA_APP_ID", "abc def", "doesn't look like a key"),
    ("ADZUNA_APP_ID", "a=b", "doesn't look like a key"),
])
def test_invalid_input_rejected(env_file, name, value, msg):
    with pytest.raises(secrets.SecretError, match=msg):
        secrets.save_secret(name, value)


def test_mask_never_reveals_key():
    assert secrets.mask("sk-ant-1234567890abcd") == "saved (…abcd)"
    assert secrets.mask(None) == "not set"
