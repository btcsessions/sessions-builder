"""Render a saved plan as a portable Markdown recording outline.

Standalone on purpose: the future Umbrel/Hermes recording-handoff job
renders the same document, so this must stay a pure function of the
plan dict with no Flask or app-state dependencies.
"""
from __future__ import annotations

import re
from datetime import datetime


def render_plan_markdown(plan: dict, channel_name: str = "") -> str:
    full = plan.get("full_plan") or {}
    lines = []

    title = (plan.get("chosen_title") or plan.get("topic") or "Untitled plan").strip()
    lines.append(f"# {title}")
    lines.append("")

    meta = []
    if channel_name:
        meta.append(channel_name)
    if plan.get("topic") and plan["topic"].strip() != title:
        meta.append(f"Topic: {plan['topic'].strip()}")
    if plan.get("video_type"):
        meta.append(f"Format: {plan['video_type']}")
    meta.append(f"Exported: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(" · ".join(meta))
    lines.append("")

    hook = (full.get("intro_hook") or "").strip()
    if hook:
        lines.append("## Intro Hook")
        lines.append("")
        for hook_line in hook.splitlines():
            lines.append(f"> {hook_line}")
        lines.append("")

    outline = full.get("outline") or []
    if outline:
        lines.append("## Outline")
        lines.append("")
        for sec in outline:
            heading = (sec.get("section") or "").strip() or "Section"
            duration = (sec.get("duration_hint") or "").strip()
            lines.append(f"### {heading}" + (f" ({duration})" if duration else ""))
            lines.append("")
            script = (sec.get("section_script") or "").strip()
            if script:
                lines.append(f"> **On-camera opener:** {script}")
                lines.append("")
            for point in sec.get("points") or []:
                lines.append(f"- {point}")
            if sec.get("points"):
                lines.append("")

    description = (full.get("description") or "").strip()
    if description:
        lines.append("## Description (paste into YouTube)")
        lines.append("")
        lines.append(description)
        lines.append("")

    tags = full.get("tags") or []
    yt_tags_csv = (full.get("yt_tags_csv") or "").strip()
    if tags or yt_tags_csv:
        lines.append("## Tags")
        lines.append("")
        lines.append(yt_tags_csv if yt_tags_csv else ", ".join(tags))
        lines.append("")

    links = full.get("links") or []
    link_lines = []
    for link in links:
        if isinstance(link, dict):
            url = (link.get("url") or "").strip()
            label = (link.get("label") or link.get("title") or "").strip()
            if url:
                link_lines.append(f"- {label}: {url}" if label else f"- {url}")
        elif isinstance(link, str) and link.strip():
            link_lines.append(f"- {link.strip()}")
    if link_lines:
        lines.append("## Links")
        lines.append("")
        lines.extend(link_lines)
        lines.append("")

    thumbs = [t.strip() for t in (full.get("thumbnail_ideas") or []) if t and t.strip()]
    if thumbs:
        lines.append("## Thumbnail Ideas")
        lines.append("")
        for t in thumbs:
            lines.append(f"- {t}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_filename(plan: dict) -> str:
    title = (plan.get("chosen_title") or plan.get("topic") or "plan").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", title).strip("-")[:60] or "plan"
    return f"recording-outline-{slug}-{datetime.now().strftime('%Y%m%d')}.md"
