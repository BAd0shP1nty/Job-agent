"""Chunk the resume and job descriptions into evidence units with provenance metadata."""
from __future__ import annotations

from dataclasses import dataclass, field

from screening.text_utils import split_sentences


@dataclass
class Chunk:
    chunk_id: str
    text: str
    document: str            # "resume" | "job:<job key>"
    section: str | None = None
    page: int | None = None
    start: int | None = None
    end: int | None = None
    metadata: dict = field(default_factory=dict)


def chunk_resume(resume_text: str, sections: list[dict] | None = None, max_chars: int = 500) -> list[Chunk]:
    """Split the resume by section, then by line groups up to ``max_chars``.

    ``sections`` is a list of {name, start, end, page}; when absent the whole text
    is treated as one section.
    """
    sections = sections or [{"name": "Resume", "start": 0, "end": len(resume_text), "page": None}]
    chunks: list[Chunk] = []
    for sec in sections:
        start, end = sec["start"], sec["end"]
        body = resume_text[start:end]
        cursor = 0
        buf_start = None
        buf: list[str] = []
        for line in body.split("\n"):
            line_start = body.find(line, cursor)
            cursor = line_start + len(line)
            if not line.strip():
                continue
            if buf and sum(len(b) for b in buf) + len(line) > max_chars:
                chunks.append(_make(resume_text, "resume", sec, start + buf_start, buf, len(chunks)))
                buf, buf_start = [], None
            if buf_start is None:
                buf_start = line_start
            buf.append(line)
        if buf:
            chunks.append(_make(resume_text, "resume", sec, start + buf_start, buf, len(chunks)))
    return chunks


def _make(full_text: str, document: str, sec: dict, abs_start: int, lines: list[str], idx: int) -> Chunk:
    text = "\n".join(lines)
    return Chunk(chunk_id=f"{document}-{idx}", text=text, document=document, section=sec.get("name"),
                 page=sec.get("page"), start=abs_start, end=abs_start + len(text))


def chunk_job(description: str, job_key: str, max_chars: int = 400) -> list[Chunk]:
    """Group consecutive sentences of a job description into chunks with char offsets."""
    chunks: list[Chunk] = []
    group: list = []
    for sentence in split_sentences(description):
        if group and sum(len(s.text) for s in group) + len(sentence.text) > max_chars:
            chunks.append(_job_chunk(description, job_key, group, len(chunks)))
            group = []
        group.append(sentence)
    if group:
        chunks.append(_job_chunk(description, job_key, group, len(chunks)))
    return chunks


def _job_chunk(description: str, job_key: str, group: list, idx: int) -> Chunk:
    start, end = group[0].start, group[-1].end
    return Chunk(chunk_id=f"job-{job_key}-{idx}", text=description[start:end], document=f"job:{job_key}",
                 section="job description", start=start, end=end)
