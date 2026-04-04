import anthropic


def _build_system_prompt(video_data: list[dict]) -> str:
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

    return f"""You are a YouTube content strategist helping a creator plan their next videos. You have access to their channel's video performance data.

**Channel Stats Summary:**
- Total videos analyzed: {total}
- Average views: {avg_views:,}
- Average engagement rate: {avg_engagement}%
- Top 3 by views: {top3}
- Bottom 3 by views: {bottom3}

**Video Performance Data:**
{truncation_note}
| Title | Views | Likes | Comments | Engagement | Published |
|-------|-------|-------|----------|------------|-----------|
{table}

Use this data to:
- Identify what topics, formats, or styles perform best
- Suggest new video ideas based on proven successes
- Spot trends in timing, engagement, or topic performance
- Give specific, actionable recommendations backed by the data

Be conversational, specific, and reference actual video titles and numbers when making points."""


def chat_with_claude(
    user_message: str,
    video_data: list[dict],
    chat_history: list[dict],
    api_key: str,
) -> str:
    """Send a message to Claude with video performance context."""
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = _build_system_prompt(video_data)

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
