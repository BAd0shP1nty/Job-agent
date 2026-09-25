"""Logging configuration.

Log records never contain resume text or full job descriptions. ``redact`` is a
best-effort guard that masks e-mail addresses and phone numbers in messages.
"""
from __future__ import annotations

import logging
import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{8,}\d)(?!\d)")
_configured = False


def redact(message: str) -> str:
    message = _EMAIL.sub("[email]", message)
    return _PHONE.sub("[phone]", message)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def configure_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger("jobhunt")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"jobhunt.{name}")
