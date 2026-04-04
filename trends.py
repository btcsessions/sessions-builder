"""Analyze video data to extract trends and patterns without needing Analytics API."""

import re
from collections import defaultdict
from datetime import datetime


def analyze_trends(video_data: list[dict]) -> dict:
    """Compute trend insights from playlist video data."""
    if not video_data:
        return {}

    trends = {}

    # --- Title pattern analysis ---
    patterns = {
        "How to / Tutorial": r"(?i)\b(how to|tutorial|guide|step.by.step|walkthrough|setup|install)\b",
        "Listicle": r"(?i)\b(top \d+|\d+ best|\d+ ways|\d+ things|\d+ reasons)\b",
        "Question": r"(?i)^(what|why|how|is |are |can |should |do |does |will )",
        "Review / First Look": r"(?i)\b(review|first look|first impressions|hands.on|unboxing)\b",
        "News / Update": r"(?i)\b(update|news|just released|announced|breaking|new )\b",
        "Comparison": r"(?i)\b(vs\.?|versus|compared|comparison|better than)\b",
    }

    pattern_stats = {}
    for label, regex in patterns.items():
        matching = [v for v in video_data if re.search(regex, v["title"])]
        if matching:
            avg_views = sum(v["view_count"] for v in matching) // len(matching)
            avg_engagement = round(sum(v["engagement_rate"] for v in matching) / len(matching), 2)
            pattern_stats[label] = {
                "count": len(matching),
                "avg_views": avg_views,
                "avg_engagement": avg_engagement,
                "best": max(matching, key=lambda v: v["view_count"])["title"],
            }

    overall_avg_views = sum(v["view_count"] for v in video_data) // len(video_data)
    # Sort by avg_views descending
    trends["title_patterns"] = dict(
        sorted(pattern_stats.items(), key=lambda x: x[1]["avg_views"], reverse=True)
    )
    trends["overall_avg_views"] = overall_avg_views

    # --- Publish day of week analysis ---
    day_stats = defaultdict(list)
    for v in video_data:
        try:
            dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
            day_name = dt.strftime("%A")
            day_stats[day_name].append(v)
        except (ValueError, KeyError):
            pass

    day_performance = {}
    for day, vids in day_stats.items():
        avg_views = sum(v["view_count"] for v in vids) // len(vids)
        day_performance[day] = {
            "count": len(vids),
            "avg_views": avg_views,
        }
    trends["publish_days"] = dict(
        sorted(day_performance.items(), key=lambda x: x[1]["avg_views"], reverse=True)
    )

    # --- Monthly/quarterly performance over time ---
    monthly = defaultdict(list)
    for v in video_data:
        try:
            dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
            key = dt.strftime("%Y-%m")
            monthly[key].append(v)
        except (ValueError, KeyError):
            pass

    monthly_trend = []
    for month in sorted(monthly.keys()):
        vids = monthly[month]
        avg_views = sum(v["view_count"] for v in vids) // len(vids)
        avg_engagement = round(sum(v["engagement_rate"] for v in vids) / len(vids), 2)
        monthly_trend.append({
            "month": month,
            "videos_published": len(vids),
            "avg_views": avg_views,
            "avg_engagement": avg_engagement,
            "total_views": sum(v["view_count"] for v in vids),
        })
    trends["monthly"] = monthly_trend

    # --- Hidden gems: high engagement, lower views ---
    median_views = sorted(v["view_count"] for v in video_data)[len(video_data) // 2]
    hidden_gems = [
        v for v in video_data
        if v["engagement_rate"] > (sum(v2["engagement_rate"] for v2 in video_data) / len(video_data)) * 1.5
        and v["view_count"] < median_views
    ]
    hidden_gems.sort(key=lambda v: v["engagement_rate"], reverse=True)
    trends["hidden_gems"] = [
        {"title": v["title"], "views": v["view_count"], "engagement": v["engagement_rate"]}
        for v in hidden_gems[:10]
    ]

    # --- Breakout hits: views much higher than average ---
    breakout_threshold = overall_avg_views * 2
    breakouts = [v for v in video_data if v["view_count"] > breakout_threshold]
    breakouts.sort(key=lambda v: v["view_count"], reverse=True)
    trends["breakout_hits"] = [
        {
            "title": v["title"],
            "views": v["view_count"],
            "multiplier": round(v["view_count"] / overall_avg_views, 1),
            "engagement": v["engagement_rate"],
        }
        for v in breakouts[:10]
    ]

    # --- Title length analysis ---
    short_titles = [v for v in video_data if len(v["title"]) <= 40]
    medium_titles = [v for v in video_data if 40 < len(v["title"]) <= 70]
    long_titles = [v for v in video_data if len(v["title"]) > 70]

    title_length = {}
    for label, group in [("Short (≤40 chars)", short_titles), ("Medium (41-70)", medium_titles), ("Long (>70)", long_titles)]:
        if group:
            title_length[label] = {
                "count": len(group),
                "avg_views": sum(v["view_count"] for v in group) // len(group),
            }
    trends["title_length"] = title_length

    # --- Upload frequency ---
    if len(monthly_trend) >= 2:
        avg_per_month = round(sum(m["videos_published"] for m in monthly_trend) / len(monthly_trend), 1)
        trends["avg_uploads_per_month"] = avg_per_month

    return trends


def trends_to_prompt_section(trends: dict) -> str:
    """Convert trend analysis into a prompt section for the AI."""
    if not trends:
        return ""

    sections = ["\n**Content Trend Analysis:**"]

    # Title patterns
    tp = trends.get("title_patterns", {})
    overall_avg = trends.get("overall_avg_views", 0)
    if tp:
        sections.append("\nTitle Format Performance (avg views vs channel avg of {:,}):".format(overall_avg))
        for label, stats in tp.items():
            diff = stats["avg_views"] - overall_avg
            direction = "above" if diff > 0 else "below"
            sections.append(
                f"  - {label}: {stats['avg_views']:,} avg views ({abs(diff):,} {direction} avg), "
                f"{stats['count']} videos, {stats['avg_engagement']}% engagement"
            )

    # Best publish days
    days = trends.get("publish_days", {})
    if days:
        best_days = list(days.items())[:3]
        sections.append("\nBest Publishing Days:")
        for day, stats in best_days:
            sections.append(f"  - {day}: {stats['avg_views']:,} avg views ({stats['count']} videos)")

    # Title length
    tl = trends.get("title_length", {})
    if tl:
        sections.append("\nTitle Length Impact:")
        for label, stats in sorted(tl.items(), key=lambda x: x[1]["avg_views"], reverse=True):
            sections.append(f"  - {label}: {stats['avg_views']:,} avg views ({stats['count']} videos)")

    # Breakout hits
    breakouts = trends.get("breakout_hits", [])
    if breakouts:
        sections.append("\nBreakout Hits (2x+ above average):")
        for b in breakouts[:5]:
            sections.append(
                f"  - \"{b['title'][:55]}\" — {b['views']:,} views ({b['multiplier']}x avg)"
            )

    # Hidden gems
    gems = trends.get("hidden_gems", [])
    if gems:
        sections.append("\nHidden Gems (high engagement, below-median views — topics worth revisiting):")
        for g in gems[:5]:
            sections.append(
                f"  - \"{g['title'][:55]}\" — {g['views']:,} views, {g['engagement']}% engagement"
            )

    # Upload frequency
    freq = trends.get("avg_uploads_per_month")
    if freq:
        sections.append(f"\nUpload Frequency: {freq} videos/month average")

    return "\n".join(sections)
