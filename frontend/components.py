"""Shared UI components and theme.

All external text (job titles, companies, descriptions) is HTML-escaped before it
is placed inside custom HTML, and links are restricted to http(s) URLs.
"""
from __future__ import annotations

import html

import streamlit as st

from config.settings import parse_iso, utcnow

PALETTE = {
    "primary": "#4F46E5",
    "accent": "#06B6D4",
    "success": "#10B981",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    "muted": "#64748B",
}

CSS = """
<style>
:root {
  --jh-primary: #4F46E5; --jh-accent: #06B6D4; --jh-success: #10B981; --jh-warning: #F59E0B;
  --jh-danger: #EF4444; --jh-muted: #64748B; --jh-card: #FFFFFF; --jh-border: #E2E8F0; --jh-text: #0F172A;
  --jh-soft: #F1F5F9;
}
@media (prefers-color-scheme: dark) {
  :root { --jh-card: #111827; --jh-border: #1F2937; --jh-text: #E5E7EB; --jh-soft: #1F2937; --jh-muted: #94A3B8; }
}
.block-container { padding-top: 1.6rem; max-width: 1280px; }
.jh-hero { background: linear-gradient(120deg, #4F46E5 0%, #7C3AED 45%, #06B6D4 100%); color: #fff;
  padding: 1.2rem 1.4rem; border-radius: 16px; margin-bottom: 1.1rem; box-shadow: 0 8px 24px rgba(79,70,229,.25); }
.jh-hero h1 { font-size: 1.55rem; margin: 0 0 .2rem 0; color: #fff; }
.jh-hero p { margin: 0; opacity: .92; font-size: .95rem; }
.jh-stat { background: var(--jh-card); border: 1px solid var(--jh-border); border-radius: 14px; padding: .9rem 1rem;
  border-top: 4px solid var(--c, #4F46E5); height: 100%; }
.jh-stat .label { color: var(--jh-muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
.jh-stat .value { color: var(--jh-text); font-size: 1.6rem; font-weight: 700; line-height: 1.3; }
.jh-stat .sub { color: var(--jh-muted); font-size: .78rem; }
.jh-card { background: var(--jh-card); border: 1px solid var(--jh-border); border-radius: 14px; padding: 1rem 1.1rem;
  margin-bottom: .35rem; border-left: 5px solid var(--c, #4F46E5); }
.jh-card h3 { margin: 0 0 .15rem 0; font-size: 1.08rem; color: var(--jh-text); }
.jh-card .meta { color: var(--jh-muted); font-size: .86rem; margin-bottom: .45rem; }
.jh-card .summary { color: var(--jh-text); font-size: .9rem; margin: .45rem 0; }
.jh-badge { display: inline-block; padding: .12rem .55rem; border-radius: 999px; font-size: .74rem; font-weight: 600;
  margin: 0 .3rem .3rem 0; color: #fff; background: var(--b, #64748B); }
.jh-chip { display: inline-block; padding: .1rem .5rem; border-radius: 8px; font-size: .75rem; margin: 0 .25rem .25rem 0;
  background: var(--jh-soft); color: var(--jh-text); border: 1px solid var(--jh-border); }
.jh-empty { text-align: center; padding: 2.2rem 1rem; border: 2px dashed var(--jh-border); border-radius: 16px;
  color: var(--jh-muted); }
.jh-empty .icon { font-size: 2.2rem; }
.jh-empty h4 { color: var(--jh-text); margin: .4rem 0; }
.jh-evidence { font-size: .85rem; border-left: 3px solid var(--jh-accent); padding: .2rem .6rem; margin: .3rem 0;
  background: var(--jh-soft); border-radius: 0 8px 8px 0; color: var(--jh-text); }
section[data-testid="stSidebar"] .stButton > button { width: 100%; justify-content: flex-start; border-radius: 10px; }
.jh-brand { font-weight: 800; font-size: 1.1rem; background: linear-gradient(90deg,#4F46E5,#06B6D4);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
</style>
"""

STATUS_COLORS = {
    "pending": "#6366F1", "duplicate_review": "#F59E0B", "selected": "#0EA5E9", "applied": "#10B981",
    "eligible": "#10B981", "ineligible": "#EF4444", "requires_verification": "#F59E0B", "rejected": "#EF4444",
    "unverified": "#F59E0B", "duplicate": "#64748B", "working": "#10B981", "untested": "#64748B",
    "needs_configuration": "#F59E0B", "requires_authorized_access": "#A855F7", "blocked": "#EF4444",
    "failing": "#EF4444", "error": "#EF4444", "high": "#10B981", "medium": "#F59E0B", "low": "#EF4444",
    "remote": "#06B6D4", "hybrid": "#8B5CF6", "office": "#0EA5E9", "unknown": "#64748B", "test": "#DB2777",
    "completed": "#10B981", "completed_with_errors": "#F59E0B", "failed": "#EF4444", "interrupted": "#F59E0B",
    "running": "#6366F1", "quota_exhausted": "#F59E0B", "flagged": "#F59E0B", "below_threshold": "#64748B",
}


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def safe_url(url: str | None) -> str | None:
    if url and isinstance(url, str) and url.lower().startswith(("https://", "http://")):
        return url
    return None


def badge(label: str, kind: str | None = None) -> str:
    color = STATUS_COLORS.get((kind or label).lower().replace(" ", "_"), "#64748B")
    return f'<span class="jh-badge" style="--b:{color}">{esc(label)}</span>'


def chips(items: list[str], limit: int = 12) -> str:
    shown = "".join(f'<span class="jh-chip">{esc(i)}</span>' for i in items[:limit])
    more = f'<span class="jh-chip">+{len(items) - limit}</span>' if len(items) > limit else ""
    return shown + more


def hero(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="jh-hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div>', unsafe_allow_html=True)


def stat_card(label: str, value, sub: str = "", color: str = "#4F46E5") -> str:
    return (f'<div class="jh-stat" style="--c:{color}"><div class="label">{esc(label)}</div>'
            f'<div class="value">{esc(value)}</div><div class="sub">{esc(sub)}</div></div>')


def empty_state(icon: str, title: str, message: str) -> None:
    st.markdown(f'<div class="jh-empty"><div class="icon">{esc(icon)}</div><h4>{esc(title)}</h4>'
                f'<div>{esc(message)}</div></div>', unsafe_allow_html=True)


def evidence(text: str) -> str:
    return f'<div class="jh-evidence">{esc(text)}</div>'


def fmt_time(value: str | None, with_time: bool = True) -> str:
    dt = parse_iso(value)
    if not dt:
        return "—"
    local = dt.astimezone()
    return local.strftime("%d %b %Y, %H:%M" if with_time else "%d %b %Y")


def relative(value: str | None) -> str:
    dt = parse_iso(value)
    if not dt:
        return "never"
    delta = utcnow() - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def days_left(expires_at: str | None) -> int | None:
    dt = parse_iso(expires_at)
    if not dt:
        return None
    return max(0, (dt - utcnow()).days + (1 if (dt - utcnow()).seconds else 0))


def navigate(page: str) -> None:
    st.session_state["page"] = page


__all__ = ["inject_css", "esc", "safe_url", "badge", "chips", "hero", "stat_card", "empty_state", "evidence",
           "fmt_time", "relative", "days_left", "navigate"]
