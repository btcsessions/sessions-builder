"""Render a saved plan as a portable Markdown recording outline.

Standalone on purpose: the future Umbrel/Hermes recording-handoff job
renders the same document, so this must stay a pure function of the
plan dict with no Flask or app-state dependencies.
"""
from __future__ import annotations

import re
from datetime import datetime

SEPARATOR = "————————————————————"
TIMESTAMPS_MARKER = "PASTE TIMESTAMPS HERE"


def _sponsor_blurb(entry: dict) -> str:
    """Blurb copy is contractual, so it is used verbatim when set.

    Kept as a local duplicate of brainstorm._build_sponsor_block's per-entry
    logic so this module stays import-light (see docstring).
    """
    blurb = (entry.get("blurb") or "").strip()
    if blurb:
        return blurb
    title = (entry.get("title") or "").strip()
    url = (entry.get("url") or "").strip()
    return f"This episode is sponsored by {title} — {url}" if title and url else ""


def _link_line(link) -> str:
    if isinstance(link, dict):
        url = (link.get("url") or "").strip()
        label = (link.get("label") or link.get("title") or "").strip()
        if not url or url == "[LINK]":
            return ""
        return f"{label}: {url}" if label else url
    if isinstance(link, str) and link.strip():
        return link.strip()
    return ""


def scrub_description_body(description: str, strip_texts: list = None) -> str:
    """Reduce a description to its prose body.

    Removes anything assemble_youtube_description re-adds: the given texts
    (sponsor blurbs, link lines), [LINK ...] placeholder lines, the
    timestamps marker, and leftover blank-line runs.
    """
    body = description or ""
    for text in strip_texts or []:
        if text:
            body = body.replace(text, "")
    body = re.sub(r"^.*\[LINK[^\]]*\].*$", "", body, flags=re.MULTILINE)
    body = body.replace(TIMESTAMPS_MARKER, "")
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def assemble_youtube_description(body: str, links: list = None, sponsors: list = None) -> str:
    """Assemble the final paste-into-YouTube description.

    One LINKS section: video links first, then selected sponsors' blurbs.
    """
    link_lines = [l for l in (_link_line(link) for link in links or []) if l]
    blurbs = []
    for s in sponsors or []:
        b = _sponsor_blurb(s)
        if b and b not in blurbs:
            blurbs.append(b)

    parts = [body.strip()] if body and body.strip() else []
    if link_lines or blurbs:
        blocks = (["\n".join(link_lines)] if link_lines else []) + blurbs
        parts.append(SEPARATOR + "\nLINKS\n\n" + "\n\n".join(blocks))
    parts.append(SEPARATOR + "\n" + TIMESTAMPS_MARKER)
    return "\n\n".join(parts)


def render_plan_markdown(plan: dict, channel_name: str = "",
                         sponsors: list = None, known_sponsor_blurbs: list = None) -> str:
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
    links = full.get("links") or []
    if description or links or sponsors:
        # Checkbox/link state is the source of truth: scrub anything the
        # assembler re-adds out of the saved prose, then rebuild the single
        # LINKS section (video links first, sponsor blurbs second).
        strip_texts = list(known_sponsor_blurbs or [])
        strip_texts += [_sponsor_blurb(s) for s in sponsors or []]
        strip_texts += [_link_line(link) for link in links]
        body = scrub_description_body(description, strip_texts)
        lines.append("## Description (paste into YouTube)")
        lines.append("")
        lines.append(assemble_youtube_description(body, links, sponsors))
        lines.append("")

    tags = full.get("tags") or []
    yt_tags_csv = (full.get("yt_tags_csv") or "").strip()
    if tags or yt_tags_csv:
        lines.append("## Tags")
        lines.append("")
        lines.append(yt_tags_csv if yt_tags_csv else ", ".join(tags))
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
