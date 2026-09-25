"""Helpers for handling untrusted external text (job pages, descriptions)."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
_INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)",
    r"disregard (all |any )?(the )?(previous|prior|above)",
    r"you are (now )?(an?|the) (ai|assistant|language model)",
    r"system prompt",
    r"</?(system|assistant|instructions?)>",
    r"mark (this|the) (job|candidate|listing) as (eligible|a match|verified)",
    r"(output|return|respond with) (only )?(json|the following)",
]
_INJECTION = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def html_to_text(raw: str | None) -> str:
    """Convert (possibly entity-escaped) HTML to plain text with paragraph breaks."""
    if not raw:
        return ""
    text = html.unescape(raw) if "&lt;" in raw else raw
    if "<" in text and ">" in text:
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
            tag.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for block in soup.find_all(["p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol"]):
            block.insert_after("\n")
        text = soup.get_text(" ")
    return sanitize_text(text)


def sanitize_text(text: str | None, max_chars: int = 60000) -> str:
    """Strip control / bidi characters and normalize whitespace. Never executes anything."""
    text = _CONTROL.sub(" ", text or "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()[:max_chars]


def detect_injection(text: str) -> list[str]:
    """Return suspicious instruction-like phrases found in untrusted text."""
    return sorted({m.group(0) for m in _INJECTION.finditer(text or "")})


@dataclass
class Sentence:
    text: str
    start: int
    end: int


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(•\-])|\n+")


def split_sentences(text: str) -> list[Sentence]:
    sentences: list[Sentence] = []
    pos = 0
    for piece in _SENT_SPLIT.split(text or ""):
        if piece is None:
            continue
        idx = text.find(piece, pos)
        if idx < 0:
            idx = pos
        stripped = piece.strip(" •-*\t")
        if stripped:
            offset = piece.find(stripped)
            sentences.append(Sentence(stripped, idx + max(offset, 0), idx + max(offset, 0) + len(stripped)))
        pos = idx + len(piece)
    return sentences
