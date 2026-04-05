import json
import anthropic
from trends import analyze_trends, trends_to_prompt_section


def generate_trend_report(
    video_data: list[dict],
    competitors_data: list[dict],
    trends_data: dict,
    api_key: str,
    analytics_data: dict = None,
    market_data: dict = None,
) -> dict:
    """Generate an AI-powered trend report with actionable advice."""
    client = anthropic.Anthropic(api_key=api_key)

    # Build a data summary for Claude
    own_avg = trends_data.get("own_avg_views", 0)
    total_vids = trends_data.get("total_video_count", 0)
    recent_count = trends_data.get("recent_video_count", 0)
    recent_avg = trends_data.get("recent_avg_views", 0)

    # Competitor summary
    comp_lines = []
    for cs in trends_data.get("competitor_summary", []):
        comp_lines.append(
            f"  - {cs['name']} ({cs['category']}): {cs['avg_views']:,} avg views, "
            f"{cs['avg_engagement']}% eng, {cs['recent_count']} videos in last 90 days"
        )
    comp_section = "\n".join(comp_lines) if comp_lines else "  None tracked"

    # Recent breakouts
    breakout_lines = []
    for rb in trends_data.get("recent_breakouts", [])[:8]:
        breakout_lines.append(
            f"  - \"{rb['title'][:60]}\" by {rb['source']} — {rb['views']:,} views ({rb['published']})"
        )
    breakout_section = "\n".join(breakout_lines) if breakout_lines else "  None"

    # Hot keywords
    kw_lines = []
    for kw, data in list(trends_data.get("hot_keywords", {}).items())[:15]:
        kw_lines.append(f"  - \"{kw}\": {data['count']} videos, {data['avg_views']:,} avg views")
    kw_section = "\n".join(kw_lines) if kw_lines else "  None"

    # Title pattern performance
    pattern_lines = []
    for label, stats in trends_data.get("title_patterns", {}).items():
        pattern_lines.append(
            f"  - {label}: {stats['avg_views']:,} avg views, {stats['count']} videos "
            f"({stats['own_count']} yours, {stats['comp_count']} competitors)"
        )
    pattern_section = "\n".join(pattern_lines) if pattern_lines else "  None"

    # Recent patterns
    recent_pattern_lines = []
    for label, stats in trends_data.get("recent_patterns", {}).items():
        recent_pattern_lines.append(f"  - {label}: {stats['avg_views']:,} avg views, {stats['count']} videos")
    recent_pattern_section = "\n".join(recent_pattern_lines) if recent_pattern_lines else "  None"

    # Own breakouts
    own_breakout_lines = []
    for ob in trends_data.get("breakout_hits", [])[:5]:
        own_breakout_lines.append(f"  - \"{ob['title'][:55]}\" — {ob['views']:,} views ({ob['multiplier']}x avg)")
    own_breakout_section = "\n".join(own_breakout_lines) if own_breakout_lines else "  None"

    # Hidden gems
    gem_lines = []
    for g in trends_data.get("hidden_gems", [])[:5]:
        gem_lines.append(f"  - \"{g['title'][:55]}\" — {g['views']:,} views, {g['engagement']}% eng")
    gem_section = "\n".join(gem_lines) if gem_lines else "  None"

    system = """You are a YouTube content strategist specializing in current platform trends.
You are analyzing data from a creator's channel AND their competitors to generate a comprehensive,
actionable trend report focused on what's working RIGHT NOW.

You must respond with ONLY valid JSON (no markdown, no code fences):

{
  "current_landscape": "2-3 paragraph summary of what's happening across these channels right now — what formats are trending, what topics are hot, what patterns you see in the recent breakout hits",
  "topic_opportunities": [
    {"topic": "specific topic idea", "why": "why this is timely and has potential", "format": "suggested video format"},
    ...5-7 topics
  ],
  "title_trends": {
    "summary": "What title styles are currently driving the most clicks based on the data",
    "tactics": ["specific title tactic 1 with example", "tactic 2 with example", "tactic 3 with example"],
    "avoid": ["title pattern to avoid and why"]
  },
  "thumbnail_trends": {
    "summary": "Current YouTube thumbnail trends that apply to this niche",
    "tactics": ["specific thumbnail tactic 1", "tactic 2", "tactic 3"],
    "examples": ["describe a thumbnail concept for a specific topic", "another concept"]
  },
  "format_recommendations": [
    {"format": "video format type", "why": "why it's working now", "tip": "specific execution tip"},
    ...3-5 formats
  ],
  "competitor_insights": "2-3 paragraph analysis of what competitors are doing well that the creator should learn from, and gaps they're leaving that the creator could fill",
  "action_plan": ["immediate action 1 - most impactful thing to try next", "action 2", "action 3", "action 4", "action 5"]
}

RULES:
- Be extremely specific — reference actual video titles and numbers from the data
- Focus on CURRENT trends (last 90 days data) not historical
- Thumbnail advice should reflect real YouTube trends (text overlays, facial expressions, contrast, etc.)
- Topic opportunities should be timely and specific to this creator's niche
- The action plan should be immediately actionable, not generic advice
- Consider what's working for competitors that this creator hasn't tried yet"""

    user_msg = f"""Here is the complete data for my channel and competitors:

**MY CHANNEL:**
- {trends_data.get('own_video_count', 0)} videos analyzed, {own_avg:,} avg views
- Recent (90 days): {recent_count} videos, {recent_avg:,} avg views

**COMPETITORS:**
{comp_section}

**TITLE FORMAT PERFORMANCE (all channels combined):**
{pattern_section}

**RECENT TITLE PATTERNS (last 90 days):**
{recent_pattern_section}

**HOT KEYWORDS (appearing in 3+ videos):**
{kw_section}

**RECENT BREAKOUT HITS (last 90 days, 2x+ above average):**
{breakout_section}

**MY BREAKOUT HITS:**
{own_breakout_section}

**MY HIDDEN GEMS (high engagement, low views):**
{gem_section}
{_build_market_section(market_data)}
Generate a comprehensive trend report with actionable advice for my next videos. Factor in the current Bitcoin market sentiment when making recommendations."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _build_market_section(market_data: dict = None) -> str:
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
    lines.append("IMPORTANT: This creator is in the bitcoin/freedom tech niche. Market sentiment significantly affects viewership.")
    lines.append("- In bear markets: focus on evergreen educational content, self-custody, privacy, and practical tools")
    lines.append("- In bull markets: capitalize on heightened interest with beginner content, trending topics, and timely coverage")
    lines.append("- Score video performance relative to the market phase they were published in, not just overall averages")
    lines.append("")
    return "\n".join(lines)


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


def _build_system_prompt(video_data: list[dict], analytics_data: dict = None, competitors_data: list = None, market_data: dict = None) -> str:
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

    # Build trends section from video data
    trends_data = analyze_trends(video_data)
    trends_section = trends_to_prompt_section(trends_data)

    return f"""You are a YouTube content strategist helping a creator plan their next videos. You have access to their channel's video performance data, trend analysis, and analytics.

**Channel Stats Summary:**
- Total videos analyzed: {total}
- Average views: {avg_views:,}
- Average engagement rate: {avg_engagement}%
- Top 3 by views: {top3}
- Bottom 3 by views: {bottom3}
{analytics_section}{trends_section}
{_build_market_section(market_data)}
**Video Performance Data:**
{truncation_note}
| Title | Views | Likes | Comments | Engagement | Published |
|-------|-------|-------|----------|------------|-----------|
{table}
{_build_competitors_section(competitors_data)}
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
- For bitcoin/crypto competitors: identify topic gaps and areas to go deeper or differentiate
- Give specific, actionable recommendations backed by the data with actual numbers

Always reference specific titles, numbers, and patterns when making points. Be direct and opinionated — the creator wants clear guidance, not hedged suggestions."""


def chat_with_claude(
    user_message: str,
    video_data: list[dict],
    chat_history: list[dict],
    api_key: str,
    analytics_data: dict = None,
    competitors_data: list = None,
    market_data: dict = None,
    plan_state: dict = None,
) -> str:
    """Send a message to Claude with video performance context."""
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = _build_system_prompt(video_data, analytics_data, competitors_data, market_data)

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
        if plan_state.get("outline"):
            plan_section += "\nOutline:"
            for sec in plan_state["outline"]:
                plan_section += f"\n  - {sec.get('section', '')}: {', '.join(sec.get('points', []))}"
        if plan_state.get("tags"):
            plan_section += f"\nTags: {', '.join(plan_state['tags'])}"

        plan_section += """

You can see and modify this plan. When the user asks you to change something about the plan (titles, outline, hook, tags, thumbnails, description), respond with your explanation AND include the changes in a fenced code block tagged `plan_change` containing valid JSON. Only include the fields being changed.

Example — if the user says "make the titles shorter":
```plan_change
{"titles": ["Short Title 1", "Short Title 2", "Short Title 3"]}
```

Available fields: titles (array of strings), thumbnail_ideas (array), intro_hook (string), outline (array of {section, points, duration_hint}), tags (array), description (string).

IMPORTANT: Only include `plan_change` blocks when the user is explicitly asking to modify the plan. For general discussion, just respond normally. Format your regular responses with clean markdown — use headers, bullet points, and bold for readability."""

        system_prompt += plan_section

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


def _build_title_history_section(title_history: list = None) -> str:
    """Build a prompt section from saved title preferences."""
    if not title_history:
        return ""
    recent = title_history[-30:]  # Last 30 saved titles
    lines = ["\n**CREATOR'S SAVED TITLE PREFERENCES (titles they liked and saved):**"]
    for entry in recent:
        used_marker = " [USED]" if entry.get("used") else ""
        lines.append(f"  - \"{entry['title']}\" (topic: {entry.get('topic', '?')}, format: {entry.get('format', '?')}, score: {entry.get('score', '?')}){used_marker}")
    lines.append("")
    lines.append("Study these saved titles carefully. They represent the creator's preferred style, word choices, and structure. New title suggestions should reflect similar patterns while remaining fresh and unique.")
    return "\n".join(lines)


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
    market_data: dict = None,
    trend_context: dict = None,
    trend_intel_context: str = "",
    title_history: list = None,
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

    channel_context = _build_system_prompt(video_data, analytics_data, competitors_data, market_data)

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
{past_titles}
{_build_title_history_section(title_history)}"""

    # Build trend context section if coming from Trends remix
    trend_section = ""
    if trend_context:
        trend_section = f"""
**INSPIRATION VIDEO:**
This plan is inspired by: "{trend_context.get('title', '')}" from {trend_context.get('source', 'Unknown')} ({trend_context.get('category', '')})
Analysis: {trend_context.get('analysis_summary', '')}"""
        remix_ideas = trend_context.get("remix_ideas", [])
        if remix_ideas:
            ideas_text = "\n".join(f"  - {r.get('title_concept', '')}: {r.get('angle', '')}" for r in remix_ideas[:5])
            trend_section += f"\nRemix ideas already generated:\n{ideas_text}"
        style_takeaways = trend_context.get("style_takeaways", [])
        if style_takeaways:
            trend_section += "\nStyle takeaways: " + "; ".join(style_takeaways[:4])
        trend_section += "\nIncorporate what works from this video into the plan."

    # Real-time trend intelligence
    intel_section = ""
    if trend_intel_context:
        intel_section = f"""
**CURRENT TRENDS & MARKET INTELLIGENCE:**
{trend_intel_context}
Use this real-time data to make the plan timely and relevant. Reference specific trending topics where natural."""

    user_msg = f"""Generate a full video plan:

**Type:** {video_type}
**Topic:** {topic}

**Supporting Links:**
{links_text}

**Competitor Videos to Draw From:**
{competitors_text}

**General Notes:**
{notes_text}
{trend_section}
{intel_section}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    # Handle if Claude wraps in code fences despite instructions
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def regenerate_titles(
    video_type: str,
    topic: str,
    notes: str,
    video_data: list[dict],
    api_key: str,
    market_data: dict = None,
    trend_context: dict = None,
    title_history: list = None,
) -> list[str]:
    """Generate fresh title suggestions without regenerating the full plan."""
    client = anthropic.Anthropic(api_key=api_key)

    # Build context from top-performing titles
    top_titles = ""
    if video_data:
        top_titles = "\n".join(
            f"- \"{v['title']}\" ({v['view_count']:,} views)"
            for v in sorted(video_data, key=lambda x: x["view_count"], reverse=True)[:15]
        )

    market_section = _build_market_section(market_data)
    history_section = _build_title_history_section(title_history)

    trend_section = ""
    if trend_context:
        trend_section = f"\nInspired by: \"{trend_context.get('title', '')}\" from {trend_context.get('source', '')} ({trend_context.get('category', '')})"

    system = f"""You are a YouTube title specialist for a bitcoin/freedom tech channel (BTC Sessions).
Generate 5 new title options optimized for click-through rate.

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
- Every title must be relevant to bitcoin, freedom tech, privacy, or self-custody
- Make each title distinctly different in structure and angle
- Do NOT suggest generic or clickbait titles — they must be substantive

Respond with ONLY a JSON array of 5 title strings. No markdown, no code fences."""

    user_msg = f"Generate 5 fresh title options for a {video_type} video about: {topic}"
    if notes:
        user_msg += f"\nNotes: {notes}"
    if trend_section:
        user_msg += trend_section

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def analyze_own_video(
    video: dict,
    video_data: list[dict],
    api_key: str,
    analytics_data: dict = None,
    competitors_data: list = None,
    market_data: dict = None,
) -> dict:
    """Analyze one of the creator's own videos with performance insights."""
    client = anthropic.Anthropic(api_key=api_key)

    avg_views = sum(v["view_count"] for v in video_data) // len(video_data) if video_data else 0
    avg_engagement = round(sum(v["engagement_rate"] for v in video_data) / len(video_data), 2) if video_data else 0

    # Performance classification
    if avg_views > 0:
        ratio = video["view_count"] / avg_views
        if ratio >= 2:
            performance = f"BREAKOUT ({ratio:.1f}x your channel average)"
        elif ratio >= 1.2:
            performance = f"STRONG (above average, {ratio:.1f}x)"
        elif ratio <= 0.5:
            performance = f"UNDERPERFORMED ({ratio:.1f}x your average)"
        else:
            performance = f"AVERAGE ({ratio:.1f}x)"
    else:
        performance = "UNKNOWN"

    # Top and bottom for context
    top5 = "\n".join(
        f"  - \"{v['title'][:55]}\" — {v['view_count']:,} views, {v['engagement_rate']}% eng"
        for v in video_data[:5]
    )
    bottom5 = "\n".join(
        f"  - \"{v['title'][:55]}\" — {v['view_count']:,} views, {v['engagement_rate']}% eng"
        for v in video_data[-5:]
    )

    # Analytics context for this video if available
    analytics_note = ""
    if analytics_data:
        top_vids = analytics_data.get("top_videos", [])
        for tv in top_vids:
            if tv["video_id"] == video["video_id"]:
                avg_pct = tv.get("avg_view_percentage", 0)
                avg_dur = round(tv.get("avg_view_duration_seconds", 0) / 60, 1)
                subs = tv.get("subscribers_gained", 0)
                analytics_note = f"\n**Analytics Data:** {avg_dur}min avg watch time, {avg_pct}% retention, +{subs} subscribers gained"
                break

    market_section = _build_market_section(market_data)

    # Market phase info for this specific video
    video_phase = video.get("_market_phase", "unknown")
    video_market_score = video.get("_market_score", 0)
    video_btc_price = video.get("_btc_price_at_publish", 0)
    phase_avg = video.get("_phase_avg", avg_views)
    bear_avg_views = video.get("_bear_avg", avg_views)
    bull_avg_views = video.get("_bull_avg", avg_views)

    market_perf_note = ""
    if video_phase != "unknown" and video_market_score > 0:
        if video_market_score >= 2:
            market_perf_note = f"BREAKOUT for a {video_phase} market video ({video_market_score}x the {video_phase} avg of {phase_avg:,} views)"
        elif video_market_score >= 1.2:
            market_perf_note = f"STRONG for a {video_phase} market video ({video_market_score}x the {video_phase} avg of {phase_avg:,} views)"
        elif video_market_score <= 0.5:
            market_perf_note = f"UNDERPERFORMED even for a {video_phase} market video ({video_market_score}x the {video_phase} avg of {phase_avg:,} views)"
        else:
            market_perf_note = f"AVERAGE for a {video_phase} market video ({video_market_score}x the {video_phase} avg of {phase_avg:,} views)"

    system = f"""You are a YouTube content strategist specializing in the bitcoin/freedom tech niche. A creator is analyzing one of their own videos to understand its performance and learn from it.

**Channel Overview:**
- Average views: {avg_views:,}
- Average engagement: {avg_engagement}%
- Top performers:
{top5}
- Lowest performers:
{bottom5}
- Bear market average views: {bear_avg_views:,}
- Bull market average views: {bull_avg_views:,}
{market_section}
You must respond with ONLY valid JSON (no markdown, no code fences):

{{
  "performance_summary": "1-2 sentence summary of overall performance",
  "market_context": "How the BTC market conditions at the time of publishing affected this video's performance. Reference the specific market phase and price. Compare to phase-specific averages, not just overall averages.",
  "title_analysis": "Analysis of the title — what works or doesn't, CTR tactics used",
  "topic_analysis": "Why this topic likely performed the way it did given market conditions",
  "format_tactics": ["what worked well 1", "what worked well 2", "what could improve"],
  "takeaways": ["actionable lesson 1", "actionable lesson 2", "actionable lesson 3"],
  "video_ideas": ["follow-up or sequel idea 1", "related topic idea 2", "idea 3"]
}}

RULES:
- Be specific and reference actual numbers
- The market_context field MUST analyze how BTC market conditions at publish time affected this video. Compare performance to the phase-specific average, not just overall.
- For BREAKOUT/STRONG videos: identify what drove the success. Suggest how to double down on this type of content — apply the same winning formula to OTHER bitcoin concepts, devices, apps, or tools. Give specific examples.
- For UNDERPERFORMING videos: be honest about what likely went wrong. Suggest how the title, angle, or timing could have been improved. Would a different framing have worked better?
- video_ideas should be concrete and specific — name actual products, tools, or concepts
- Be direct and actionable"""

    market_line = ""
    if video_phase != "unknown":
        market_line = f"\n**Market Phase at Publish:** {video_phase.upper()}"
        if video_btc_price:
            market_line += f" (BTC ~${video_btc_price:,.0f})"
        if market_perf_note:
            market_line += f"\n**Market-Adjusted Performance:** {market_perf_note}"

    user_msg = f"""Analyze this video from my channel:

**Title:** {video['title']}
**Views:** {video['view_count']:,}
**Likes:** {video.get('like_count', 0):,}
**Comments:** {video.get('comment_count', 0):,}
**Engagement Rate:** {video.get('engagement_rate', 0)}%
**Published:** {video.get('published_at', 'Unknown')[:10]}
**Overall Performance:** {performance}{market_line}{analytics_note}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def analyze_competitor_video(
    video: dict,
    competitor: dict,
    video_data: list[dict],
    api_key: str,
    analytics_data: dict = None,
    market_data: dict = None,
) -> dict:
    """Analyze a competitor's video and suggest takeaways for the creator."""
    client = anthropic.Anthropic(api_key=api_key)

    # Compute competitor channel averages
    comp_videos = competitor.get("videos", [])
    comp_avg_views = sum(v["view_count"] for v in comp_videos) // len(comp_videos) if comp_videos else 0
    comp_avg_engagement = round(sum(v["engagement_rate"] for v in comp_videos) / len(comp_videos), 2) if comp_videos else 0

    # Performance classification
    if comp_avg_views > 0:
        ratio = video["view_count"] / comp_avg_views
        if ratio >= 2:
            performance = f"BREAKOUT ({ratio:.1f}x their channel average)"
        elif ratio >= 1.2:
            performance = f"STRONG (above average, {ratio:.1f}x)"
        elif ratio <= 0.5:
            performance = f"UNDERPERFORMED ({ratio:.1f}x their average)"
        else:
            performance = f"AVERAGE ({ratio:.1f}x)"
    else:
        performance = "UNKNOWN"

    # Creator's own stats for comparison
    creator_avg = sum(v["view_count"] for v in video_data) // len(video_data) if video_data else 0

    # Build a condensed list of creator's top videos for context
    creator_top = "\n".join(
        f"  - \"{v['title'][:55]}\" — {v['view_count']:,} views, {v['engagement_rate']}% eng"
        for v in video_data[:10]
    )

    market_section = _build_market_section(market_data)

    system = f"""You are a YouTube content strategist. A creator is studying a competitor's video to learn from it.

**The Creator's Channel:**
- Average views: {creator_avg:,}
- Top videos:
{creator_top}

**The Competitor: {competitor['name']}** (Category: {competitor.get('category', 'Unknown')})
- Average views: {comp_avg_views:,}
- Average engagement: {comp_avg_engagement}%
{market_section}
You must respond with ONLY valid JSON (no markdown, no code fences):

{{
  "performance_summary": "1-2 sentence summary of how this video performed relative to the channel",
  "title_analysis": "Analysis of the title — what works or doesn't, CTR tactics used",
  "topic_analysis": "Why this topic likely performed the way it did",
  "format_tactics": ["tactic 1", "tactic 2", "tactic 3"],
  "takeaways": ["actionable takeaway 1 for the creator", "actionable takeaway 2", "actionable takeaway 3"],
  "video_ideas": ["specific video idea the creator could make inspired by this", "another idea"]
}}

RULES:
- Be specific and reference actual numbers
- For breakout/strong videos: identify what likely drove the success
- For underperformers: identify what likely went wrong
- Tailor takeaways to the creator's niche and channel strengths
- Video ideas should be adapted for the creator's audience, not carbon copies"""

    user_msg = f"""Analyze this competitor video:

**Title:** {video['title']}
**Views:** {video['view_count']:,}
**Likes:** {video.get('like_count', 0):,}
**Comments:** {video.get('comment_count', 0):,}
**Engagement Rate:** {video.get('engagement_rate', 0)}%
**Published:** {video.get('published_at', 'Unknown')[:10]}
**Performance:** {performance}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def suggest_channels(
    video_data: list[dict],
    competitors_data: list[dict],
    api_key: str,
    market_data: dict = None,
) -> dict:
    """Suggest YouTube channels to follow for inspiration."""
    client = anthropic.Anthropic(api_key=api_key)

    topic_sample = "\n".join(
        f"  - {v['title'][:70]}" for v in video_data[:15]
    )

    existing = [c["name"] for c in competitors_data] if competitors_data else []
    existing_str = ", ".join(existing) if existing else "None"

    market_section = _build_market_section(market_data)

    system = f"""You are a YouTube growth strategist. A bitcoin/freedom tech creator wants to discover new channels to learn from — both crypto-native channels and mainstream tech channels that are currently doing well.

**The Creator's Recent Content:**
{topic_sample}

**Channels Already Tracked:** {existing_str}
{market_section}
You must respond with ONLY valid JSON (no markdown, no code fences):

{{
  "crypto_channels": [
    {{
      "name": "Channel Name",
      "handle": "@handle",
      "why": "1 sentence on why they're worth watching right now",
      "learn": "What specific tactic or approach to study"
    }}
  ],
  "tech_channels": [
    {{
      "name": "Channel Name",
      "handle": "@handle",
      "why": "1 sentence on why they're worth watching right now",
      "learn": "What specific tactic or approach to study"
    }}
  ]
}}

RULES:
- Suggest 5-6 crypto/bitcoin channels and 4-5 mainstream tech channels
- Do NOT suggest channels already tracked: {existing_str}
- Focus on channels that are CURRENTLY doing well — growing, getting high engagement, producing consistently
- For crypto channels: include bitcoin-focused, privacy/freedom tech, and broader crypto education channels
- For tech channels: include channels whose format, editing, storytelling, or thumbnail strategy could be studied — even if their topic is completely different (hardware reviews, app reviews, explainers, etc.)
- The handle should be their actual YouTube @handle if you know it, otherwise best guess
- Be specific about what to learn from each — not generic praise
- Prioritize channels with strong recent momentum over legacy channels coasting on old subscribers"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": "Suggest channels I should be watching right now for inspiration. Focus on who is currently doing well and what I can learn from them."}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def remix_video(
    video_title: str,
    video_source: str,
    source_category: str,
    analysis_summary: str,
    video_data: list[dict],
    api_key: str,
    focus_topic: str = "",
    market_data: dict = None,
    trend_intel_context: str = "",
) -> dict:
    """Generate remix ideas: adapt a video's format/style for the creator's bitcoin channel."""
    client = anthropic.Anthropic(api_key=api_key)

    creator_top = "\n".join(
        f"  - \"{v['title'][:55]}\" — {v['view_count']:,} views"
        for v in video_data[:12]
    )

    market_section = _build_market_section(market_data)

    is_crypto = source_category in ("Bitcoin/Crypto", "Freedom Tech")

    if is_crypto:
        remix_angle = """This is a crypto/bitcoin video. The creator wants to DOUBLE DOWN on this format:
- Suggest ways to apply the same format, style, or angle to different bitcoin topics, devices, apps, or concepts
- Think about what made this video work and how to replicate that success with fresh subject matter
- Consider: different hardware wallets, different apps, different privacy tools, different network concepts, different use cases"""
    else:
        remix_angle = """This is a general tech video (not crypto). The creator wants to ADAPT this style for bitcoin:
- Identify what makes this video's format, structure, or presentation compelling
- Suggest how to apply that exact style/trend to bitcoin, freedom tech, privacy, or self-custody topics
- Think about: the pacing, the hook, the thumbnail approach, the narrative structure, the editing style
- Make the bitcoin version feel native to that format, not forced"""

    focus_section = ""
    if focus_topic:
        focus_section = f"""
**FOCUS TOPIC:** The creator specifically wants to apply this to: {focus_topic}
Make sure at least 2-3 suggestions directly involve "{focus_topic}" as the subject matter."""

    system = f"""You are a YouTube content strategist for a bitcoin/freedom tech creator (BTC Sessions).

This channel's core mission is helping people UNDERSTAND and USE bitcoin. Every video should be rooted in education and practical instruction. The audience ranges from total beginners to intermediate users who want to level up.

**Creator's Top Videos:**
{creator_top}

**Video Being Remixed:** "{video_title}"
**Source:** {video_source} ({source_category})
**Analysis Context:** {analysis_summary}
{market_section}
{trend_intel_context}
{remix_angle}
{focus_section}

**CONTENT FRAMING — all suggestions must be educational/instructional in nature. Think:**
- "The Best Tools For..." (best wallets for privacy, best apps for Lightning, best hardware for running a node)
- "How To Use..." (how to use Sparrow Wallet, how to use CoinJoin, how to use a signing device)
- "Best Tech Stack For..." (best privacy stack, best self-custody stack, best Lightning stack)
- "Using [Device] With [App]..." (using Coldcard with Sparrow, using SeedSigner with Specter)
- "First Impressions of..." (first impressions of a new wallet, new hardware, new privacy tool)
- "Privacy/Security Setup With..." (full privacy setup with Whirlpool, securing your bitcoin with multisig)
- "Complete Guide To..." (complete guide to self-custody, running your own node, Lightning channels)
- "X vs Y" comparisons (Coldcard vs Trezor, Sparrow vs BlueWallet, Lightning vs on-chain for daily use)
- "What Happens When..." explainers (what happens when you send bitcoin, what happens in a coinjoin)
- General tech video styles adapted to bitcoin (unboxings, tier lists, "I tried X for 30 days", day-in-the-life with bitcoin-only)

Be creative beyond these examples, but always anchor in practical bitcoin education. The goal is to bring new users in while still providing value to existing bitcoiners. Avoid hype, price speculation, or shilling — focus on sovereignty, understanding, and practical use.

You must respond with ONLY valid JSON (no markdown, no code fences):

{{
  "remix_ideas": [
    {{
      "title_concept": "A working title for the remix video",
      "angle": "1-2 sentences on the specific angle and why it works",
      "format_notes": "How to adapt the original's format/style"
    }}
  ],
  "style_takeaways": [
    "Specific production/style element to borrow (thumbnail approach, pacing, hook style, etc.)"
  ]
}}

RULES:
- Generate 4-5 remix ideas, each with a concrete title concept
- Generate 3-4 style takeaways
- Every suggestion must be educational, instructional, or explainer content about bitcoin/freedom tech
- Title concepts should be real titles the creator could use, not placeholders
- If a focus topic is given, prioritize it heavily
- Consider current market conditions and trending topics when suggesting — lean into what's timely
- Balance content for new users (onboarding, first steps) with content for existing bitcoiners (advanced setups, optimizations)
- Never suggest price prediction, trading, or speculation content"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": f"Remix this video for my bitcoin channel: \"{video_title}\""}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)
