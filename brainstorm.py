from __future__ import annotations

import json
import re
import anthropic
from llm import llm_chat
from trends import analyze_trends, trends_to_prompt_section


DEFAULT_CHANNEL_NICHE = (
    "A YouTube creator running a freedom tech channel. The scope includes bitcoin "
    "and extends to related freedom technologies: self-hosted AI, online privacy, "
    "sovereign computing (hosting your own data on local servers), private "
    "communications, and other DIY sovereignty tools. Content is primarily "
    "tutorials, explainers, and hands-on walkthroughs. The audience values "
    "self-sovereignty, practical education, and doing things themselves."
)


def _effective_niche(niche: str | None) -> str:
    """Return the caller-supplied niche or the default if blank."""
    return (niche or "").strip() or DEFAULT_CHANNEL_NICHE


def _creator_context_block(niche: str | None) -> str:
    """Formatted block describing the creator's channel for prompt preambles."""
    return f"**About the creator's channel:**\n{_effective_niche(niche)}"


def _extract_urls(text: str) -> list[str]:
    """Extract HTTP/HTTPS URLs from user message text."""
    return re.findall(r'https?://[^\s<>"\')\]]+', text)


def _video_type_rule(video_type: str) -> str:
    """Prompt line describing the video format — explicit or AI-inferred."""
    if (video_type or "").strip():
        return f"This is a **{video_type}** video — tailor the structure, pacing, and tone accordingly"
    return ("No format was specified — INFER the best video format (tutorial, comparison, list, "
            "setup walkthrough, first impressions, explainer, news reaction, ...) from the topic "
            "and notes, put it in the \"video_type\" field, and tailor structure, pacing, and tone to it")


def _parse_plan_json(text: str) -> dict:
    """Parse a JSON plan from model output, tolerating fences or stray prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the outermost JSON object in the text
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _affiliates_only(links: list) -> list:
    """Library entries the AI may weave into content (excludes sponsors)."""
    return [l for l in (links or []) if (l.get("type") or "affiliate") != "sponsor"]


def _build_link_library_section(affiliate_links: list = None) -> str:
    """Format the creator's locally held referral/affiliate links for prompts."""
    affiliate_links = _affiliates_only(affiliate_links)
    if not affiliate_links:
        return ""
    lines = ["\n**CREATOR'S LINK LIBRARY (referral/affiliate links — use the REAL URLs):**"]
    for l in affiliate_links:
        title = (l.get("title") or "").strip()
        url = (l.get("url") or "").strip()
        if title and url:
            lines.append(f"- {title}: {url}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _build_sponsor_block(sponsors: list) -> str:
    """Deterministic sponsor section for the description.

    Sponsor copy is often contractual, so it is never AI-paraphrased: the
    blurb is used verbatim when set, otherwise a plain name+link line.
    """
    lines = []
    for s in sponsors or []:
        blurb = (s.get("blurb") or "").strip()
        title = (s.get("title") or "").strip()
        url = (s.get("url") or "").strip()
        if blurb:
            lines.append(blurb)
        elif title and url:
            lines.append(f"This episode is sponsored by {title} — {url}")
    return "\n\n".join(lines)


def _fetch_url_text(url: str, timeout: int = 10) -> str:
    """Fetch a URL and return a cleaned text summary (max ~3000 chars)."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type and "text" not in content_type:
                return f"[Non-text content: {content_type}]"
            raw = resp.read(200_000).decode("utf-8", errors="replace")

        # Strip HTML tags, scripts, styles
        raw = re.sub(r'<script[^>]*>[\s\S]*?</script>', ' ', raw, flags=re.IGNORECASE)
        raw = re.sub(r'<style[^>]*>[\s\S]*?</style>', ' ', raw, flags=re.IGNORECASE)
        raw = re.sub(r'<[^>]+>', ' ', raw)
        # Collapse whitespace
        raw = re.sub(r'\s+', ' ', raw).strip()
        # Truncate
        if len(raw) > 3000:
            raw = raw[:3000] + "..."
        return raw
    except Exception as e:
        return f"[Failed to fetch: {e}]"


def _build_market_section(market_data: dict = None, creator_niche: str = "") -> str:
    if not market_data:
        return ""
    info = market_data.get("info", {})
    summary = market_data.get("summary", {})
    if not info.get("price"):
        return ""

    lines = ["\n**Bitcoin Market Sentiment:**"]
    lines.append(f"- Current BTC price: ${info['price']:,.0f}")
    lines.append(f"- 200-day MA: ${info.get('ma_200', 0):,.0f}")
    lines.append(f"- Current phase: {info.get('phase', 'unknown').upper()}")

    bull = summary.get("bull", {})
    bear = summary.get("bear", {})
    if bull.get("count"):
        lines.append(f"- Bull market videos: {bull['count']} videos, {bull['avg_views']:,} avg views, {bull['avg_engagement']}% eng")
    if bear.get("count"):
        lines.append(f"- Bear market videos: {bear['count']} videos, {bear['avg_views']:,} avg views, {bear['avg_engagement']}% eng")

    lines.append("")
    lines.append(f"IMPORTANT — creator context: {_effective_niche(creator_niche)}")
    lines.append("Bitcoin market sentiment can affect viewership for bitcoin-adjacent content — weight its relevance based on the niche above.")
    lines.append("- In bear markets: focus on evergreen educational content, self-custody, privacy, and practical tools")
    lines.append("- In bull markets: capitalize on heightened interest with beginner content, trending topics, and timely coverage")
    lines.append("- Score video performance relative to the market phase they were published in, not just overall averages")
    lines.append("")
    return "\n".join(lines)


def _build_competitors_section(competitors_data: list = None, creator_niche: str = "") -> str:
    if not competitors_data:
        return ""

    sections = []

    # Separate own-channel entries (by role) from regular competitors.
    # own_role: "current" = the new channel these plans are FOR;
    # "legacy" = the creator's previous main channel (is_own_channel migrates here).
    def _role(c):
        return c.get("own_role") or ("legacy" if c.get("is_own_channel") else "")

    current_channels = [c for c in competitors_data if _role(c) == "current"]
    legacy_channels = [c for c in competitors_data if _role(c) == "legacy"]
    regular_comps = [c for c in competitors_data if not _role(c)]

    def _own_channel_lines(comp, max_vids):
        videos = comp.get("videos", [])
        if not videos:
            return None
        avg_views = sum(v["view_count"] for v in videos) // len(videos)
        video_lines = "\n".join(
            f"    - \"{v['title'][:65]}\" — {v['view_count']:,} views, {v['engagement_rate']}% eng"
            for v in videos[:max_vids]
        )
        return (f"  **{comp['name']}** (avg {avg_views:,} views, {len(videos)} recent videos)",
                f"  Top performers:\n{video_lines}\n")

    # The new channel — the one being planned for. Strongest signal as it grows.
    if current_channels:
        sections.append("\n**YOUR NEW CHANNEL — THE CHANNEL THESE PLANS ARE FOR (TOP-PRIORITY SIGNAL):**")
        sections.append("*These are uploads on the creator's NEW channel — the one every plan is being made for. Weight this data above everything else, scaled by how much of it there is: with only a few videos treat it as directional, and as the catalog grows let it dominate over the legacy-channel patterns below.*\n")
        for comp in current_channels:
            lines = _own_channel_lines(comp, max_vids=10)
            if lines:
                sections.extend(lines)

    # The legacy channel — proven audience data, fading as the new channel grows
    if legacy_channels:
        sections.append("\n**YOUR PREVIOUS MAIN CHANNEL (STRONG BUT FADING REFERENCE):**")
        sections.append("*These are the creator's videos on their previous main channel. The audience overlaps heavily and has proven what it clicks on — treat these as a strong signal for framing, titles, and packaging. BUT they lose relevance as the new channel accumulates its own history: whenever the new channel's data (above) disagrees, the new channel wins.*\n")
        for comp in legacy_channels:
            lines = _own_channel_lines(comp, max_vids=10)
            if lines:
                sections.extend(lines)

    # Group regular competitors by category
    by_category = {}
    for comp in regular_comps:
        cat = comp.get("category", "Other")
        by_category.setdefault(cat, []).append(comp)

    category_instructions = {
        "Mainstream Tech": f"Study their formats, production style, pacing, and engagement tactics. Consider how to adapt their successful approaches for the creator's niche: {_effective_niche(creator_niche)}",
        "Bitcoin/Crypto": "Look for topic gaps, content they cover that you don't, and areas where you can go deeper or offer a unique perspective.",
        "Freedom Tech": "Identify underserved topics in privacy, self-sovereignty, and open-source tech that you could cover better or differently.",
    }

    for cat, comps in by_category.items():
        instruction = category_instructions.get(cat, f"Analyze their content for useful patterns and opportunities.")
        sections.append(f"\n**Competitor Channels — {cat}:**")
        sections.append(f"*Strategy: {instruction}*\n")

        for comp in comps:
            videos = comp.get("videos", [])
            if not videos:
                continue
            avg_views = sum(v["view_count"] for v in videos) // len(videos)
            top5 = videos[:5]
            video_lines = "\n".join(
                f"    - \"{v['title'][:55]}\" — {v['view_count']:,} views, {v['engagement_rate']}% eng"
                for v in top5
            )
            sections.append(f"  **{comp['name']}** (avg {avg_views:,} views, {len(videos)} recent videos)")
            sections.append(f"  Top performers:\n{video_lines}\n")

    return "\n".join(sections)


def _build_system_prompt(video_data: list[dict], analytics_data: dict = None, competitors_data: list = None, market_data: dict = None, creator_niche: str = "") -> str:
    if not video_data:
        return (
            "You are a YouTube content strategist. The user hasn't loaded any video data yet. "
            "Help them brainstorm video ideas and content strategy."
        )

    total = len(video_data)
    avg_views = sum(v["view_count"] for v in video_data) // total
    avg_engagement = round(sum(v["engagement_rate"] for v in video_data) / total, 2)

    # Build a condensed table — show all if <= 50, otherwise top/bottom 20
    if total <= 50:
        table_videos = video_data
    else:
        table_videos = video_data[:20] + video_data[-20:]

    rows = []
    for v in table_videos:
        rows.append(
            f"| {v['title'][:60]} | {v['view_count']:,} | {v['like_count']:,} | "
            f"{v['comment_count']:,} | {v['engagement_rate']}% | {v['published_at'][:10]} |"
        )

    table = "\n".join(rows)
    truncation_note = ""
    if total > 50:
        truncation_note = f"\n(Showing top 20 and bottom 20 of {total} total videos)\n"

    top3 = ", ".join(v["title"][:50] for v in video_data[:3])
    bottom3 = ", ".join(v["title"][:50] for v in video_data[-3:])

    # Build analytics section if available
    analytics_section = ""
    if analytics_data:
        ch = analytics_data.get("channel", {})
        if ch:
            watch_hours = round(ch.get("watch_time_minutes", 0) / 60, 1)
            avg_duration = round(ch.get("avg_view_duration_seconds", 0) / 60, 1)
            analytics_section += f"""
**Channel Analytics (last 12 months):**
- Total views: {ch.get('views', 0):,}
- Watch time: {watch_hours:,} hours
- Average view duration: {avg_duration} minutes
- Net subscribers gained: {ch.get('net_subscribers', 0):,} ({ch.get('subscribers_gained', 0):,} gained, {ch.get('subscribers_lost', 0):,} lost)
- Shares: {ch.get('shares', 0):,}
"""
            # Add impressions + CTR if available
            if ch.get("impressions"):
                analytics_section += f"- Total impressions: {ch['impressions']:,}\n"
                analytics_section += f"- Channel-wide CTR: {ch.get('impressions_ctr', 0)}%\n"

        traffic = analytics_data.get("traffic_sources", [])
        if traffic:
            traffic_lines = "\n".join(
                f"  - {t['source']}: {t['views']:,} views, {round(t['watch_time_minutes']/60, 1)} hours"
                for t in traffic[:7]
            )
            analytics_section += f"\n**Traffic Sources:**\n{traffic_lines}\n"

        top_vids = analytics_data.get("top_videos", [])
        if top_vids:
            top_lines = []
            for tv in top_vids[:15]:
                # Try to find title from video_data
                title = tv["video_id"]
                for v in video_data:
                    if v["video_id"] == tv["video_id"]:
                        title = v["title"][:50]
                        break
                avg_pct = tv.get("avg_view_percentage", 0)
                avg_dur = round(tv.get("avg_view_duration_seconds", 0) / 60, 1)
                ctr = tv.get("impressions_ctr", 0)
                impressions = tv.get("impressions", 0)
                ctr_str = f", {ctr}% CTR ({impressions:,} imp)" if impressions else ""
                top_lines.append(
                    f"  - {title}: {avg_dur}min avg watch, {avg_pct}% retention, +{tv.get('subscribers_gained', 0)} subs{ctr_str}"
                )
            analytics_section += f"\n**Top Videos by Watch Time (with retention, CTR & subs gained):**\n" + "\n".join(top_lines) + "\n"

        # Retention curves — summarize key drop-off points for videos that have curve data
        retention_curves = analytics_data.get("retention_curves", {})
        if retention_curves:
            curve_lines = []
            for vid_id, points in retention_curves.items():
                if not points:
                    continue
                # Find video title
                title = vid_id
                for v in video_data:
                    if v["video_id"] == vid_id:
                        title = v["title"][:45]
                        break
                # Extract key retention milestones
                r_30s = r_mid = r_75 = None
                for p in points:
                    if r_30s is None and p["elapsed_pct"] >= 5:  # ~first 30s of a 10min video
                        r_30s = p["audience_pct"]
                    if r_mid is None and p["elapsed_pct"] >= 50:
                        r_mid = p["audience_pct"]
                    if r_75 is None and p["elapsed_pct"] >= 75:
                        r_75 = p["audience_pct"]
                # Relative retention (vs similar YT videos): >1 = better than avg
                avg_relative = 0
                rel_points = [p["relative_retention"] for p in points if p.get("relative_retention")]
                if rel_points:
                    avg_relative = round(sum(rel_points) / len(rel_points), 2)

                parts = []
                if r_30s is not None:
                    parts.append(f"5%: {r_30s}%")
                if r_mid is not None:
                    parts.append(f"50%: {r_mid}%")
                if r_75 is not None:
                    parts.append(f"75%: {r_75}%")
                if avg_relative:
                    rel_label = "above avg" if avg_relative > 1 else "below avg"
                    parts.append(f"vs YouTube: {avg_relative}x ({rel_label})")
                if parts:
                    curve_lines.append(f"  - {title}: {', '.join(parts)}")

            if curve_lines:
                analytics_section += f"\n**Retention Curves (audience still watching at each point):**\n"
                analytics_section += "  (Format: elapsed % of video → % of viewers still watching)\n"
                analytics_section += "\n".join(curve_lines[:20]) + "\n"
                analytics_section += "\n  KEY INSIGHT: Compare retention between tutorials vs non-tutorials. If non-tutorials hold viewers past 50% at much higher rates, the tutorial packaging (hook, pacing, visual variety) likely needs work — not the content itself.\n"

        monthly = analytics_data.get("monthly_trend", [])
        if monthly:
            month_lines = "\n".join(
                f"  - {m['month']}: {m['views']:,} views, {round(m['watch_time_minutes']/60, 1)}h watch time, +{m['subscribers_gained']} subs"
                for m in monthly[-6:]
            )
            analytics_section += f"\n**Monthly Trend (last 6 months):**\n{month_lines}\n"

    # Build trends section from video data
    trends_data = analyze_trends(video_data)
    trends_section = trends_to_prompt_section(trends_data)

    return f"""You are a YouTube content strategist helping a creator plan their next videos. You have access to their channel's video performance data, trend analysis, and analytics.

{_creator_context_block(creator_niche)}

**Channel Stats Summary:**
- Total videos analyzed: {total}
- Average views: {avg_views:,}
- Average engagement rate: {avg_engagement}%
- Top 3 by views: {top3}
- Bottom 3 by views: {bottom3}
{analytics_section}{trends_section}
{_build_market_section(market_data, creator_niche)}
**Video Performance Data:**
{truncation_note}
| Title | Views | Likes | Comments | Engagement | Published |
|-------|-------|-------|----------|------------|-----------|
{table}
{_build_competitors_section(competitors_data, creator_niche)}
Use this data to:
- Identify what topics, formats, or styles perform best
- Recommend title formats that drive higher CTR based on the title pattern analysis (e.g. if "How to" titles outperform, lean into that)
- Recommend optimal title length based on what performs best
- Suggest the best days to publish based on historical performance
- Identify breakout hit patterns — what do the 2x+ videos have in common?
- Highlight hidden gem topics worth revisiting (high engagement but low views = underexposed good content)
- Suggest new video ideas based on proven successes
- Analyze retention and watch time patterns to recommend ideal video length and pacing
- Consider traffic sources when recommending SEO and promotion strategies
- Compare against competitor channels to find gaps and opportunities
- For mainstream tech competitors: study their formats, pacing, and engagement tactics to adapt for your niche
- For topic-adjacent competitors (channels covering topics that overlap with the creator's niche above): identify topic gaps and areas to go deeper or differentiate
- Give specific, actionable recommendations backed by the data with actual numbers
- If the user shares a URL, read the fetched content and use it to form specific, actionable video angle recommendations tied to the creator's niche
- If real-time trend intelligence is available below, reference specific trending topics, community discussions, and current events where relevant

**IMPORTANT — Own-Channel Non-Tutorial Videos:**
If you see a section labeled "YOUR OWN CHANNEL — NON-TUTORIAL VIDEOS" in the data above, these are the creator's OWN recent non-tutorial videos from their main channel. Many of these have been massive outliers in views. Since the creator's existing audience is already clicking on these videos at very high rates, treat them as OVER-WEIGHTED indicators of:
- What framing and angles resonate most with this audience RIGHT NOW
- What title patterns and styles drive outsized clicks from this specific subscriber base
- How to package educational/tutorial content using the same emotional hooks, urgency, and topical framing that made these non-tutorial videos pop
Do NOT treat these as just "competitor data" — they are the strongest signal for audience appetite.

Always reference specific titles, numbers, and patterns when making points. Be direct and opinionated — the creator wants clear guidance, not hedged suggestions."""


def chat_with_claude(
    user_message: str,
    video_data: list[dict],
    chat_history: list[dict],
    settings: dict,
    analytics_data: dict = None,
    competitors_data: list = None,
    market_data: dict = None,
    plan_state: dict = None,
    trend_intel_context: str = "",
    creator_niche: str = "",
) -> str:
    """Send a message to Claude with video performance context."""

    system_prompt = _build_system_prompt(video_data, analytics_data, competitors_data, market_data, creator_niche)

    # Append real-time trend intelligence
    if trend_intel_context:
        system_prompt += f"\n\n{trend_intel_context}\n"

    # Add plan context if a plan is active
    if plan_state:
        plan_section = "\n\n**CURRENT VIDEO PLAN (visible in the planner):**"
        if plan_state.get("topic"):
            plan_section += f"\nTopic: {plan_state['topic']}"
        if plan_state.get("video_type"):
            plan_section += f"\nFormat: {plan_state['video_type']}"
        if plan_state.get("titles"):
            plan_section += "\nTitles:\n" + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(plan_state["titles"]))
        if plan_state.get("intro_hook"):
            plan_section += f"\nIntro Hook: {plan_state['intro_hook']}"
        if plan_state.get("thumbnail_ideas"):
            plan_section += "\nThumbnail ideas:\n" + "\n".join(f"  - {t}" for t in plan_state["thumbnail_ideas"])
        if plan_state.get("outline"):
            plan_section += "\nOutline:"
            for sec in plan_state["outline"]:
                plan_section += f"\n  - {sec.get('section', '')}"
                if sec.get("duration_hint"):
                    plan_section += f" ({sec['duration_hint']})"
                if sec.get("section_script"):
                    plan_section += f"\n    On-camera opener: {sec['section_script']}"
                if sec.get("points"):
                    plan_section += f"\n    Points: {', '.join(sec['points'])}"
        if plan_state.get("tags"):
            plan_section += f"\nTags: {', '.join(plan_state['tags'])}"
        elif plan_state.get("yt_tags_csv"):
            plan_section += f"\nTags: {plan_state['yt_tags_csv']}"
        if plan_state.get("links"):
            link_lines = []
            for l in plan_state["links"]:
                if isinstance(l, dict):
                    link_lines.append(f"  - {l.get('label', '')}: {l.get('url', '')}".strip())
                else:
                    link_lines.append(f"  - {l}")
            plan_section += "\nLinks:\n" + "\n".join(link_lines)
        if plan_state.get("description"):
            plan_section += f"\nDescription:\n{plan_state['description']}"

        plan_section += """

You can see and modify this plan. When the user asks you to change something about the plan (titles, outline, hook, tags, thumbnails, description), respond with your explanation AND include the changes in a fenced code block tagged `plan_change` containing valid JSON. Only include the fields being changed.

Example — if the user says "make the titles shorter":
```plan_change
{"titles": ["Short Title 1", "Short Title 2", "Short Title 3"]}
```

Available fields: titles (array of strings), thumbnail_ideas (array), intro_hook (string), outline (array of {section, section_script, points, duration_hint}), tags (array), description (string).

IMPORTANT: Only include `plan_change` blocks when the user is explicitly asking to modify the plan. For general discussion, just respond normally. Format your regular responses with clean markdown — use headers, bullet points, and bold for readability."""

        system_prompt += plan_section

    # Detect URLs in the user message and fetch their content
    urls = _extract_urls(user_message)
    augmented_message = user_message
    if urls:
        fetched_parts = []
        for url in urls[:3]:  # limit to 3 URLs per message
            content = _fetch_url_text(url)
            fetched_parts.append(f"**Content from {url}:**\n{content}")
        augmented_message += "\n\n---\n" + "\n\n".join(fetched_parts)

    messages = []
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": augmented_message})

    return llm_chat(settings, system_prompt, messages, max_tokens=4000)


def _build_title_history_section(title_history: list = None) -> str:
    """Build a prompt section split into style preferences and proven performers."""
    if not title_history:
        return ""

    from datetime import datetime, timedelta
    now = datetime.now()

    # Split into two categories
    style_prefs = []     # saved but not used, or used without performance data
    proven = []          # used with real performance data

    for entry in title_history:
        try:
            saved_date = datetime.fromisoformat(entry.get("saved_at", "2020-01-01"))
        except (ValueError, TypeError):
            saved_date = now - timedelta(days=365)
        days_ago = (now - saved_date).days
        recency_weight = 2 ** (-days_ago / 90)

        if entry.get("used") and entry.get("perf_ratio"):
            perf_weight = min(3.0, max(0.3, entry["perf_ratio"]))
            proven.append((entry, recency_weight * perf_weight))
        else:
            style_prefs.append((entry, recency_weight))

    lines = []

    # Style preferences — sorted by recency weight, top 15
    if style_prefs:
        style_prefs.sort(key=lambda x: x[1], reverse=True)
        lines.append("\n**STYLE PREFERENCES (titles the creator liked — weighted by recency):**")
        for entry, weight in style_prefs[:15]:
            freshness = "fresh" if weight > 0.7 else ("recent" if weight > 0.3 else "older")
            lines.append(f"  - \"{entry['title']}\" (topic: {entry.get('topic', '?')}, format: {entry.get('format', '?')}) [{freshness}]")
        lines.append("These reflect the creator's current style preferences. Prioritize patterns from 'fresh' titles over 'older' ones — trends and tastes evolve.")

    # Proven performers — sorted by combined weight, top 15
    if proven:
        proven.sort(key=lambda x: x[1], reverse=True)
        lines.append("\n**PROVEN PERFORMERS (titles that were used and have real performance data):**")
        for entry, weight in proven[:15]:
            perf = entry.get("perf_ratio", 0)
            views = entry.get("actual_views", 0)
            phase = entry.get("market_phase", "unknown")
            if perf >= 2:
                perf_label = f"BREAKOUT {perf}x avg"
            elif perf >= 1.2:
                perf_label = f"STRONG {perf}x avg"
            elif perf <= 0.5:
                perf_label = f"UNDERPERFORMED {perf}x avg"
            else:
                perf_label = f"AVERAGE {perf}x avg"
            lines.append(f"  - \"{entry['title']}\" — {views:,} views, {perf_label}, published during {phase} market")
        lines.append("These titles have real-world performance data. Heavily favor patterns from BREAKOUT and STRONG titles. Note market phase — a 'strong' bear-market title may indicate evergreen appeal.")

    if not lines:
        return ""

    lines.append("")
    lines.append("IMPORTANT: Balance proven patterns with freshness. Don't just copy old titles — evolve the style. If a proven title used 'Complete Guide To X' and hit 2x, consider that structure but with current topics.")
    return "\n".join(lines)


def generate_video_plan(
    video_type: str,
    topic: str,
    links: list[str],
    competitors: list[str],
    notes: str,
    video_data: list[dict],
    settings: dict,
    analytics_data: dict = None,
    competitors_data: list = None,
    market_data: dict = None,
    trend_intel_context: str = "",
    title_history: list = None,
    desc_template: str = "",
    sponsor_template: str = "",
    default_yt_tags: str = "",
    sponsors: list = None,
    creator_niche: str = "",
    affiliate_links: list = None,
    use_web_search: bool = True,
) -> dict:
    """Generate a full video plan with title, thumbnail, outline, etc."""

    # Build a list of past video titles for reference links
    past_titles = ""
    if video_data:
        entries = []
        for v in video_data:
            entries.append(f"- \"{v['title']}\" ({v['view_count']:,} views) https://youtube.com/watch?v={v['video_id']}")
        past_titles = "\n".join(entries)

    channel_context = _build_system_prompt(video_data, analytics_data, competitors_data, market_data, creator_niche)

    # Build the user's input context
    links_text = "\n".join(f"- {l}" for l in links) if links else "None provided"
    competitors_text = "\n".join(f"- {c}" for c in competitors) if competitors else "None provided"
    notes_text = notes if notes else "None"

    system = f"""{channel_context}

You are now generating a complete video production plan. You must respond with ONLY valid JSON matching this exact structure (no markdown, no code fences):

{{
  "titles": ["title option 1", "title option 2", "title option 3"],
  "thumbnail_ideas": ["idea 1", "idea 2", "idea 3"],
  "intro_hook": "A compelling 2-3 sentence opening hook to grab viewers in the first 10 seconds",
  "outline": [
    {{"section": "Section name", "section_script": "1-3 spoken sentences to open this section on camera", "points": ["key point 1", "key point 2"], "duration_hint": "~2 min"}},
    {{"section": "Section name", "section_script": "1-3 spoken sentences to open this section on camera", "points": ["key point 1", "key point 2"], "duration_hint": "~3 min"}}
  ],
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"],
  "description": "Full YouTube description with summary and links to referenced past tutorials if relevant",
  "video_type": "the video format (e.g. tutorial, comparison, list, setup walkthrough, first impressions)"
}}

IMPORTANT RULES:
- {_video_type_rule(video_type)}
- For tutorials: step-by-step structure with clearly segregated sections so each part works as a self-contained, compartmentalized lesson a viewer can jump to. Practical focus.
- For first impressions: excitement/curiosity hook, unboxing flow, pros/cons, verdict
- For listicles: numbered items, punchy transitions, teaser of best item early
- Each outline section gets a "section_script": a short scripted opener (1-3 spoken sentences, in the creator's on-camera voice) that introduces what this section covers and why it matters. For tutorials these are essential — they let viewers who skip around orient instantly.
- Suggest 3 title options optimized for CTR and YouTube search
- Thumbnail ideas should describe the visual concept, text overlay, and mood
- The outline should be a realistic video skeleton with timing hints
- Tags should be relevant for YouTube SEO (8-12 tags)
- The description must be SEO-optimized: naturally work the main search keywords into the opening sentences. It should contain:
  1. A short paragraph (2-4 sentences) summarizing what the video covers and why it matters — NOT a section-by-section breakdown
  2. A list of relevant links: when a relevant past tutorial (URLs below) or link-library entry exists, use its REAL URL with a descriptive label (e.g. "Full node setup tutorial: https://..."). Only use a [LINK] placeholder for things with no known URL (e.g. "Download Umbrel: [LINK]")
  3. Include the referral/affiliate links from the creator's link library that genuinely relate to this video's topic — skip unrelated ones
  Do NOT include timestamps — those are added manually later
  Do NOT include a sponsor section — that will be auto-inserted
- For "tags": generate 8-12 topic-specific SEO tags for this video (these get COMBINED with the creator's default tags)
- If supporting links are provided, reference and incorporate them naturally in the outline and description
- If competitor videos are provided, consider what works in those videos and differentiate
- Base recommendations on what has performed well in the channel data
- If web search is available, use it (up to 3 searches) to check current facts, tool versions, and what's ranking on this topic — then fold that into the outline, titles, and SEO keywords. Your final reply must still be ONLY the JSON object.

Here are the creator's past videos for reference links:
{past_titles}
{_build_link_library_section(affiliate_links)}
{_build_title_history_section(title_history)}"""

    # Real-time trend intelligence
    intel_section = ""
    if trend_intel_context:
        intel_section = f"""
**CURRENT TRENDS & MARKET INTELLIGENCE:**
{trend_intel_context}
Use this real-time data to make the plan timely and relevant. Reference specific trending topics where natural."""

    user_msg = f"""Generate a full video plan:

**Type:** {video_type or "(not specified — infer the best format from the topic and notes)"}
**Topic:** {topic}

**Supporting Links:**
{links_text}

**Competitor Videos to Draw From:**
{competitors_text}

**General Notes:**
{notes_text}
{intel_section}"""

    text = llm_chat(settings, system, [{"role": "user", "content": user_msg}],
                    max_tokens=4000, use_web_search=use_web_search)
    plan = _parse_plan_json(text)
    if video_type:
        plan["video_type"] = video_type
    elif not plan.get("video_type"):
        plan["video_type"] = "video"

    # Auto-append sponsor block + timestamps reminder to description
    sponsor = _build_sponsor_block(sponsors) if sponsors else (sponsor_template or desc_template)
    if "description" in plan:
        desc = plan["description"]
        # Strip any AI-generated timestamp placeholders
        for placeholder in ["[TIMESTAMPS]", "[TIMESTAMP]"]:
            desc = desc.replace(placeholder, "").strip()
        # Append sponsor block then timestamps reminder
        if sponsor:
            desc = desc.rstrip() + "\n\n" + sponsor
        desc = desc.rstrip() + "\n\nPASTE TIMESTAMPS HERE"
        plan["description"] = desc

    # Build combined YouTube tags (default + generated), enforce 500 char limit
    if default_yt_tags:
        all_tags = [t.strip() for t in default_yt_tags.split(",") if t.strip()]
        generated = plan.get("tags", [])
        seen = {t.lower() for t in all_tags}
        for t in generated:
            if t.lower() not in seen:
                all_tags.append(t)
                seen.add(t.lower())
    else:
        all_tags = plan.get("tags", [])
    # Truncate to 500 chars
    csv = ""
    for t in all_tags:
        candidate = (csv + ", " + t) if csv else t
        if len(candidate) > 500:
            break
        csv = candidate
    plan["yt_tags_csv"] = csv

    return plan


def parse_notes_to_plan(
    notes: str,
    topic: str,
    video_type: str,
    settings: dict,
    existing_plan: dict = None,
    desc_template: str = "",
    sponsor_template: str = "",
    sponsors: list = None,
    default_yt_tags: str = "",
    video_data: list[dict] = None,
    market_data: dict = None,
    competitors_data: list = None,
    creator_niche: str = "",
    affiliate_links: list = None,
) -> dict:
    """Parse freeform notes/outline into a structured video plan."""

    if existing_plan:
        mode = "amend"
        existing_json = json.dumps({
            "titles": existing_plan.get("titles", []),
            "thumbnail_ideas": existing_plan.get("thumbnail_ideas", []),
            "intro_hook": existing_plan.get("intro_hook", ""),
            "outline": existing_plan.get("outline", []),
            "tags": existing_plan.get("tags", []),
            "description": existing_plan.get("description", ""),
        }, indent=2)
        context = f"""There is an EXISTING plan that should be amended/merged with the new notes.
Preserve what's already good, integrate the new information, and improve where the notes suggest changes.

**Existing plan:**
{existing_json}"""
    else:
        mode = "create"
        context = "Create the plan entirely from the notes provided."

    # Build channel + market context (same data the full planner uses)
    channel_context = ""
    if video_data:
        channel_context = _build_system_prompt(video_data, None, competitors_data, market_data, creator_niche)

    market_section = _build_market_section(market_data, creator_niche)

    # Trend analysis for what's currently working
    trend_section = ""
    if video_data:
        try:
            trends = analyze_trends(video_data, competitors_data)
            trend_section = trends_to_prompt_section(trends)
        except Exception:
            pass

    system = f"""You are a YouTube video planning assistant. Parse the user's freeform notes into a structured video plan.

{context}

{channel_context}
{market_section}
{trend_section}
{_build_link_library_section(affiliate_links)}

You must respond with ONLY valid JSON (no markdown, no code fences):

{{
  "titles": ["title option 1", "title option 2", "title option 3"],
  "thumbnail_ideas": ["idea 1", "idea 2", "idea 3"],
  "intro_hook": "A compelling 2-3 sentence opening hook",
  "outline": [
    {{"section": "Section name", "section_script": "1-3 spoken sentences to open this section on camera", "points": ["key point 1", "key point 2"], "duration_hint": "~X min"}}
  ],
  "tags": ["tag1", "tag2", "tag3"],
  "description": "A short paragraph summarizing the video, then a list of relevant links with [LINK] placeholders"
}}

RULES:
- Extract structure from the notes — section headings, bullet points, key topics
- If the notes contain title ideas, use them. If not, generate 3 based on the content.
- Use the channel performance data and market trends above to inform title angles, framing, and packaging — angle the content toward what's currently resonating with the audience
- Infer logical sections and timing from the outline depth
- Give each section a short "section_script" (1-3 spoken sentences, creator's on-camera voice) that opens the section — keep any opener the notes already contain
- The description should be a short paragraph (2-4 sentences) summarizing the video, followed by relevant links — NOT a section-by-section breakdown. Do NOT include timestamps. Use REAL URLs from the creator's link library when relevant; only use [LINK] placeholders for links with no known URL.
- Keep the creator's voice and phrasing where possible — don't over-polish their notes
- If {'amending' if existing_plan else 'creating'}: {'merge intelligently — dont discard existing work, integrate the new notes' if existing_plan else 'build the full plan from scratch based on the notes'}
- {_video_type_rule(video_type)}"""

    user_msg = f"""Parse these notes into a structured video plan:

**Video Type:** {video_type or "(not specified — infer from the notes)"}
**Topic:** {topic}

**Notes:**
{notes}"""

    text = llm_chat(settings, system, [{"role": "user", "content": user_msg}], max_tokens=3000)
    plan = _parse_plan_json(text)
    if video_type:
        plan["video_type"] = video_type
    elif not plan.get("video_type"):
        plan["video_type"] = "video"

    # Auto-append sponsor block + timestamps reminder if creating new
    sponsor = _build_sponsor_block(sponsors) if sponsors else (sponsor_template or desc_template)
    if not existing_plan and "description" in plan:
        desc = plan["description"]
        for placeholder in ["[TIMESTAMPS]", "[TIMESTAMP]"]:
            desc = desc.replace(placeholder, "").strip()
        if sponsor:
            desc = desc.rstrip() + "\n\n" + sponsor
        desc = desc.rstrip() + "\n\nPASTE TIMESTAMPS HERE"
        plan["description"] = desc

    # Build combined YouTube tags, enforce 500 char limit
    if default_yt_tags:
        all_tags = [t.strip() for t in default_yt_tags.split(",") if t.strip()]
        generated = plan.get("tags", [])
        seen = {t.lower() for t in all_tags}
        for t in generated:
            if t.lower() not in seen:
                all_tags.append(t)
                seen.add(t.lower())
    else:
        all_tags = plan.get("tags", [])
    csv = ""
    for t in all_tags:
        candidate = (csv + ", " + t) if csv else t
        if len(candidate) > 500:
            break
        csv = candidate
    plan["yt_tags_csv"] = csv

    return plan


def suggest_plan_links(
    topic: str,
    outline: list[dict],
    video_data: list[dict],
    settings: dict,
    affiliate_links: list[dict] = None,
) -> dict:
    """Suggest relevant links for a video plan — from channel, affiliates, and external."""

    # Build channel video list for Claude to search
    channel_videos = "\n".join(
        f'- "{v["title"]}" https://youtube.com/watch?v={v["video_id"]}'
        for v in video_data[:80]
    )

    affiliate_section = ""
    if affiliate_links:
        aff_list = "\n".join(f'- "{a["title"]}": {a["url"]}' for a in _affiliates_only(affiliate_links))
        affiliate_section = f"""

**Creator's affiliate/referral links (suggest relevant ones):**
{aff_list}"""

    outline_text = "\n".join(
        f"- {s.get('section', '')}: {', '.join(s.get('points', []))}"
        for s in outline
    )

    system = f"""You are helping a YouTube creator find relevant links for their video description.

**Creator's existing videos (search these for related tutorials):**
{channel_videos}
{affiliate_section}

You must respond with ONLY valid JSON (no markdown, no code fences):

{{
  "channel_links": [
    {{
      "label": "Descriptive label for the link",
      "url": "https://youtube.com/watch?v=...",
      "reason": "Why this tutorial is relevant"
    }}
  ],
  "affiliate_links": [
    {{
      "label": "Product/service name",
      "url": "the exact affiliate URL",
      "reason": "Why this affiliate link is relevant to this video"
    }}
  ],
  "external_links": [
    {{
      "label": "Product/app/website name",
      "url": "[LINK]",
      "reason": "Why this link should be in the description"
    }}
  ]
}}

RULES:
- For channel_links: find tutorials from the creator's OWN channel above that viewers would want as companion content. Only suggest videos that actually exist in the list. Use the exact YouTube URL from the list.
- For affiliate_links: pick affiliate links from the creator's list above that are RELEVANT to this specific video. Only include ones that genuinely relate to the topic — don't force irrelevant affiliates. Use the exact URL from the list.
- For external_links: identify product pages, app downloads, official websites, and tools mentioned in the outline that aren't covered by affiliate links. Use [LINK] as the URL since you don't know the exact URLs — the creator will replace these.
- Be selective — only suggest links that are genuinely useful, not filler
- Labels should be concise and descriptive (how they'd appear in a YouTube description)"""

    user_msg = f"""Find relevant links for this video:

**Topic:** {topic}

**Outline:**
{outline_text}"""

    text = llm_chat(settings, system, [{"role": "user", "content": user_msg}], max_tokens=1000).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def swap_single_title(
    current_titles: list[str],
    topic: str,
    video_type: str,
    video_data: list[dict],
    settings: dict,
    market_data: dict = None,
    title_history: list = None,
    creator_niche: str = "",
) -> str:
    """Generate one replacement title that's different from the existing options."""

    top_titles = ""
    if video_data:
        top_titles = "\n".join(
            f"- \"{v['title']}\" ({v['view_count']:,} views)"
            for v in sorted(video_data, key=lambda x: x["view_count"], reverse=True)[:10]
        )

    market_section = _build_market_section(market_data, creator_niche)
    history_section = _build_title_history_section(title_history)
    existing_str = "\n".join(f"- \"{t}\"" for t in current_titles)

    system = f"""You are a YouTube title specialist.

{_creator_context_block(creator_niche)}

**Creator's top performing titles:**
{top_titles}
{market_section}
{history_section}

**Current title options (DO NOT repeat any of these):**
{existing_str}

Generate exactly ONE new title that is distinctly different from all the options above — different angle, different format, different hook. Respond with ONLY the title text, nothing else."""

    vt_label = video_type or "video"
    return llm_chat(settings, system,
                    [{"role": "user", "content": f"Generate one fresh title for a {vt_label} video about: {topic}"}],
                    max_tokens=100).strip().strip('"')


def regenerate_titles(
    video_type: str,
    topic: str,
    notes: str,
    video_data: list[dict],
    settings: dict,
    market_data: dict = None,
    title_history: list = None,
    creator_niche: str = "",
) -> list[str]:
    """Generate fresh title suggestions without regenerating the full plan."""

    # Build context from top-performing titles
    top_titles = ""
    if video_data:
        top_titles = "\n".join(
            f"- \"{v['title']}\" ({v['view_count']:,} views)"
            for v in sorted(video_data, key=lambda x: x["view_count"], reverse=True)[:15]
        )

    market_section = _build_market_section(market_data, creator_niche)
    history_section = _build_title_history_section(title_history)

    system = f"""You are a YouTube title specialist.
Generate 5 new title options optimized for click-through rate.

{_creator_context_block(creator_niche)}

**Creator's top performing titles for reference:**
{top_titles}
{market_section}
{history_section}

RULES:
- Titles must be optimized for CTR and YouTube search
- Mix different proven title formats: how-to, listicles, questions, bold statements, comparisons
- Keep titles 40-60 characters when possible (optimal for YouTube)
- Use power words that drive clicks: best, ultimate, complete, easy, stop, never, must
- Include numbers where natural
- Consider brackets like [2026] or (Step by Step) for CTR boost
- Every title must be relevant to the creator's niche described above
- Make each title distinctly different in structure and angle
- Do NOT suggest generic or clickbait titles — they must be substantive

Respond with ONLY a JSON array of 5 title strings. No markdown, no code fences."""

    user_msg = f"Generate 5 fresh title options for a {video_type or 'video'} about: {topic}"
    if notes:
        user_msg += f"\nNotes: {notes}"

    text = llm_chat(settings, system, [{"role": "user", "content": user_msg}], max_tokens=500).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def score_titles_ai(
    titles: list[str],
    topic: str,
    video_type: str,
    video_data: list[dict],
    settings: dict,
    market_data: dict = None,
    creator_niche: str = "",
) -> list[dict]:
    """Have Claude score and critique title options with rationale."""

    top_titles = ""
    if video_data:
        top_titles = "\n".join(
            f"- \"{v['title']}\" ({v['view_count']:,} views)"
            for v in sorted(video_data, key=lambda x: x["view_count"], reverse=True)[:10]
        )

    avg_views = 0
    if video_data:
        avg_views = sum(v.get("view_count", 0) for v in video_data) / len(video_data)

    market_section = _build_market_section(market_data, creator_niche)

    titles_text = "\n".join(f'{i+1}. "{t}"' for i, t in enumerate(titles))

    system = f"""You are a YouTube title scoring expert.

{_creator_context_block(creator_niche)}

**Channel's top-performing titles:**
{top_titles}

**Channel average views:** ~{int(avg_views):,}
{market_section}

Score each title on a 0-100 scale based on:
- **Clarity of value** (30pts): Does the viewer instantly know what they'll get?
- **CTR potential** (25pts): Would this stand out in a feed and compel a click?
- **Search/discovery** (20pts): Does it contain terms people actually search for?
- **Specificity** (15pts): Does it name concrete products, tools, or outcomes vs vague claims?
- **Channel fit** (10pts): Does it match what this channel's audience expects?

You must respond with ONLY valid JSON (no markdown, no code fences) — an array of objects:
[
  {{
    "title": "the exact title",
    "score": 78,
    "rationale": "1-2 sentence explanation of the score",
    "suggestion": "Optional: brief improvement suggestion, or null if score >= 80"
  }}
]

Be honest and calibrated. A score of 50 is genuinely average. Most titles should land 55-80. Only truly excellent titles get 85+. Don't grade inflate."""

    user_msg = f"""Score these title options for a {video_type or 'video'} about: {topic}

{titles_text}"""

    text = llm_chat(settings, system, [{"role": "user", "content": user_msg}], max_tokens=1000).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def generate_plan_postmortem(
    plan: dict,
    video_data: list[dict],
    settings: dict,
) -> dict:
    """Generate a post-mortem analysis comparing a plan's predictions vs actual performance."""

    avg_views = sum(v.get("view_count", 0) for v in video_data) / len(video_data) if video_data else 1

    system = """You are a YouTube analytics coach reviewing the performance of a video that was planned with AI assistance. Compare what was planned vs what actually happened, and extract lessons for future planning.

You must respond with ONLY valid JSON (no markdown, no code fences):
{
  "verdict": "outperformed" | "met_expectations" | "underperformed",
  "summary": "2-3 sentence overall assessment",
  "title_analysis": "Did the chosen title work? Would another from the options have been better?",
  "what_worked": ["lesson 1", "lesson 2"],
  "what_to_improve": ["lesson 1", "lesson 2"],
  "keyword_insights": ["keyword or phrase to boost in future", "keyword to avoid"],
  "score_accuracy": "Was the AI score predictive? Brief assessment."
}

Be honest and specific. Reference the actual numbers."""

    user_msg = f"""Post-mortem for this planned video:

**Plan Created:** {plan.get('created_at', 'Unknown')}
**Topic:** {plan.get('topic', 'Unknown')}
**Video Type:** {plan.get('video_type', 'Unknown')}
**Chosen Title:** "{plan.get('chosen_title', '')}"
**AI Score at Planning:** {plan.get('ai_score', 'N/A')}
**Other Title Options Considered:** {', '.join(f'"{t}"' for t in plan.get('all_titles', []) if t != plan.get('chosen_title', ''))}

**Actual Results:**
**Published Title:** "{plan.get('actual_title', '')}"
**Views:** {plan.get('actual_views', 0):,}
**Channel Average:** ~{int(avg_views):,} views
**Performance Ratio:** {plan.get('perf_ratio', 0)}x average
**Engagement Rate:** {plan.get('engagement_rate', 0)}%
**Market Phase:** {plan.get('market_phase', 'unknown')}"""

    text = llm_chat(settings, system, [{"role": "user", "content": user_msg}], max_tokens=800).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def suggest_channels(
    video_data: list[dict],
    competitors_data: list[dict],
    settings: dict,
    market_data: dict = None,
    exclude_handles: list[str] = None,
    creator_channel: str = "",
    creator_niche: str = "",
) -> dict:
    """Suggest YouTube channels to follow for inspiration."""

    topic_sample = "\n".join(
        f"  - {v['title'][:70]}" for v in video_data[:15]
    )

    # Calculate creator's own average views for benchmarking
    avg_views = 0
    if video_data:
        avg_views = sum(v.get("view_count", 0) for v in video_data) / len(video_data)
        creator_stats = f"\n**Creator's Average Views Per Video:** ~{int(avg_views):,}"
    else:
        creator_stats = ""

    existing = [c["name"] for c in competitors_data] if competitors_data else []
    existing_str = ", ".join(existing) if existing else "None"

    creator_section = ""
    if creator_channel:
        creator_section = f"\n**Creator's Own Channel:** {creator_channel} — NEVER suggest this channel."

    exclude_handles = exclude_handles or []
    exclude_section = ""
    if exclude_handles:
        exclude_section = f"\n**Do NOT suggest any of these previously suggested handles:** {', '.join(exclude_handles)}"

    market_section = _build_market_section(market_data, creator_niche)

    system = f"""You are a YouTube growth strategist helping a creator discover new channels to learn from. The PRIMARY focus is tech channels covering privacy, AI, self-hosting, and DIY setups, with a smaller set of crypto/bitcoin channels on the side.

{_creator_context_block(creator_niche)}

**The Creator's Recent Content:**
{topic_sample}
{creator_stats}
{creator_section}

**Channels Already Tracked:** {existing_str}
{exclude_section}
{market_section}
You must respond with ONLY valid JSON (no markdown, no code fences):

{{
  "tech_channels": [
    {{
      "name": "Channel Name",
      "handle": "@handle",
      "why": "1 sentence on why they're worth watching right now",
      "learn": "What specific tactic or approach to study"
    }}
  ],
  "crypto_channels": [
    {{
      "name": "Channel Name",
      "handle": "@handle",
      "why": "1 sentence on why they're worth watching right now",
      "learn": "What specific tactic or approach to study"
    }}
  ]
}}

RULES:
- Suggest 7-8 tech channels (PRIMARY focus) and only 2-3 crypto/bitcoin channels (secondary)
- NEVER suggest the creator's own channel{f' ({creator_channel})' if creator_channel else ''}
- Do NOT suggest channels already tracked: {existing_str}
- {"CRITICAL: Do NOT suggest any of these handles that were already suggested: " + ", ".join(exclude_handles) + ". You MUST suggest completely different channels." if exclude_handles else ""}
- Focus on channels that are CURRENTLY doing well — growing, getting high engagement, producing consistently
- IMPORTANT: Only suggest channels pulling in views AT LEAST comparable to the creator's own average ({f'~{int(avg_views):,} views/video' if avg_views else 'unknown — aim high'}), preferably substantially more. The goal is to study channels that are outperforming, not smaller channels.
- For tech channels (the PRIMARY category): suggest the top YouTube channels covering:
  * Privacy tech — VPNs, encrypted messaging, de-Googling, threat modeling, opsec, digital sovereignty
  * AI — local/self-hosted LLMs, open-source AI tooling, AI workflows, agent frameworks, practical AI tutorials
  * Self-hosting — Homelab, Docker/Kubernetes, Proxmox, NAS builds, self-hosted replacements for SaaS, reverse proxies, networking
  * DIY setups — custom PC/server builds, network gear, 3D printing, hardware projects, maker/tinkerer content
  Include a healthy mix across all four of these buckets. These channels are the bulk of the suggestions.
- For crypto channels (the SMALLER secondary category): just 2-3 high-signal channels — either standout Bitcoin tutorial/education, privacy-focused Bitcoin tooling, or a sharp podcast covering sovereignty/geopolitics. Keep this list short.
- The handle should be their actual YouTube @handle if you know it, otherwise best guess
- Be specific about what to learn from each — not generic praise
- Prioritize channels with strong recent momentum over legacy channels coasting on old subscribers
- Suggest DIFFERENT channels each time — variety matters for discovery"""

    user_msg = "Suggest channels I should be watching right now for inspiration. The PRIMARY focus is top tech channels covering privacy, AI, self-hosting, and DIY setups — that's the bulk of what I want. Add just 2-3 crypto/bitcoin channels on the side. Tell me who's currently doing well and what I can learn from them."
    if exclude_handles:
        user_msg += f"\n\nIMPORTANT: I've already seen suggestions for these handles: {', '.join(exclude_handles)}. Give me COMPLETELY DIFFERENT channels this time — do not repeat any of those."

    text = llm_chat(settings, system, [{"role": "user", "content": user_msg}], max_tokens=2000).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)
