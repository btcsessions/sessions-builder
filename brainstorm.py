import anthropic


def _build_competitors_section(competitors_data: list = None) -> str:
    if not competitors_data:
        return ""

    sections = []
    # Group by category
    by_category = {}
    for comp in competitors_data:
        cat = comp.get("category", "Other")
        by_category.setdefault(cat, []).append(comp)

    category_instructions = {
        "Mainstream Tech": "Study their formats, production style, pacing, and engagement tactics. Consider how to adapt their successful approaches for the bitcoin/freedom tech niche.",
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


def _build_system_prompt(video_data: list[dict], analytics_data: dict = None, competitors_data: list = None) -> str:
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
                top_lines.append(
                    f"  - {title}: {avg_dur}min avg watch, {avg_pct}% retention, +{tv.get('subscribers_gained', 0)} subs"
                )
            analytics_section += f"\n**Top Videos by Watch Time (with retention & subs gained):**\n" + "\n".join(top_lines) + "\n"

        monthly = analytics_data.get("monthly_trend", [])
        if monthly:
            month_lines = "\n".join(
                f"  - {m['month']}: {m['views']:,} views, {round(m['watch_time_minutes']/60, 1)}h watch time, +{m['subscribers_gained']} subs"
                for m in monthly[-6:]
            )
            analytics_section += f"\n**Monthly Trend (last 6 months):**\n{month_lines}\n"

    return f"""You are a YouTube content strategist helping a creator plan their next videos. You have access to their channel's video performance data and analytics.

**Channel Stats Summary:**
- Total videos analyzed: {total}
- Average views: {avg_views:,}
- Average engagement rate: {avg_engagement}%
- Top 3 by views: {top3}
- Bottom 3 by views: {bottom3}
{analytics_section}
**Video Performance Data:**
{truncation_note}
| Title | Views | Likes | Comments | Engagement | Published |
|-------|-------|-------|----------|------------|-----------|
{table}
{_build_competitors_section(competitors_data)}
Use this data to:
- Identify what topics, formats, or styles perform best
- Suggest new video ideas based on proven successes
- Spot trends in timing, engagement, or topic performance
- Analyze retention and watch time patterns to recommend ideal video length and pacing
- Consider traffic sources when recommending SEO and promotion strategies
- Compare against competitor channels to find gaps and opportunities
- For mainstream tech competitors: study their formats, pacing, and engagement tactics to adapt for your niche
- For bitcoin/crypto competitors: identify topic gaps and areas to go deeper or differentiate
- Give specific, actionable recommendations backed by the data

Be conversational, specific, and reference actual video titles and numbers when making points."""


def chat_with_claude(
    user_message: str,
    video_data: list[dict],
    chat_history: list[dict],
    api_key: str,
    analytics_data: dict = None,
    competitors_data: list = None,
) -> str:
    """Send a message to Claude with video performance context."""
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = _build_system_prompt(video_data, analytics_data, competitors_data)

    messages = []
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=system_prompt,
        messages=messages,
    )

    return response.content[0].text


def generate_video_plan(
    video_type: str,
    topic: str,
    links: list[str],
    competitors: list[str],
    notes: str,
    video_data: list[dict],
    api_key: str,
    analytics_data: dict = None,
    competitors_data: list = None,
) -> dict:
    """Generate a full video plan with title, thumbnail, outline, etc."""
    client = anthropic.Anthropic(api_key=api_key)

    # Build a list of past video titles for reference links
    past_titles = ""
    if video_data:
        entries = []
        for v in video_data:
            entries.append(f"- \"{v['title']}\" ({v['view_count']:,} views) https://youtube.com/watch?v={v['video_id']}")
        past_titles = "\n".join(entries)

    channel_context = _build_system_prompt(video_data, analytics_data, competitors_data)

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
    {{"section": "Section name", "points": ["key point 1", "key point 2"], "duration_hint": "~2 min"}},
    {{"section": "Section name", "points": ["key point 1", "key point 2"], "duration_hint": "~3 min"}}
  ],
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"],
  "description": "Full YouTube description with summary and links to referenced past tutorials if relevant"
}}

IMPORTANT RULES:
- This is a **{video_type}** video — tailor the structure, pacing, and tone accordingly
- For tutorials: step-by-step structure, clear sections, practical focus
- For first impressions: excitement/curiosity hook, unboxing flow, pros/cons, verdict
- For listicles: numbered items, punchy transitions, teaser of best item early
- Suggest 3 title options optimized for CTR and YouTube search
- Thumbnail ideas should describe the visual concept, text overlay, and mood
- The outline should be a realistic video skeleton with timing hints
- Tags should be relevant for YouTube SEO (8-12 tags)
- The description should be 3-5 paragraphs, include relevant links to past tutorials from the channel when applicable
- If supporting links are provided, reference and incorporate them naturally in the outline and description
- If competitor videos are provided, consider what works in those videos and differentiate
- Base recommendations on what has performed well in the channel data

Here are the creator's past videos for reference links:
{past_titles}"""

    user_msg = f"""Generate a full video plan:

**Type:** {video_type}
**Topic:** {topic}

**Supporting Links:**
{links_text}

**Competitor Videos to Draw From:**
{competitors_text}

**General Notes:**
{notes_text}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    import json
    text = response.content[0].text.strip()
    # Handle if Claude wraps in code fences despite instructions
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)
