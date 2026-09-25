"""Thin wrapper around the Anthropic SDK for structured match explanations."""
from __future__ import annotations

import anthropic

from agent.prompts import MATCH_OUTPUT_SCHEMA, MATCH_SYSTEM_PROMPT
from config.logging_config import get_logger
from config.settings import get_settings

log = get_logger("llm")


class LLMUnavailable(RuntimeError):
    pass


class ClaudeClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        settings = get_settings()
        key = api_key or settings.anthropic_api_key
        if not key:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set.")
        self.model = model or settings.claude_model
        self.client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=120.0)

    def complete_json(self, messages: list[dict]) -> str:
        """Return the JSON text produced under the match output schema.

        Raises ``LLMUnavailable`` for errors that make the call unusable; the
        caller falls back to a deterministic explanation.
        """
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=MATCH_SYSTEM_PROMPT,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": MATCH_OUTPUT_SCHEMA}},
            )
        except anthropic.AuthenticationError as exc:
            raise LLMUnavailable("Anthropic API key was rejected.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMUnavailable("API key lacks permission for this model.") from exc
        except anthropic.NotFoundError as exc:
            raise LLMUnavailable(f"Model '{self.model}' was not found.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMUnavailable("Anthropic rate limit reached after retries.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailable(f"Anthropic API error {exc.status_code}.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable("Could not reach the Anthropic API (network).") from exc

        if response.stop_reason == "refusal":
            raise LLMUnavailable("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise LLMUnavailable("Model output was truncated.")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMUnavailable("Model returned no text output.")
        return text
