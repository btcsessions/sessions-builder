"""Analyze video data to extract trends and patterns across own and competitor channels."""

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def analyze_trends(video_data: list[dict], competitors_data: list[dict] = None) -> dict:
    """Compute trend insights from own + competitor video data."""
    if not video_data:
        return {}

    trends = {}

    # --- Combine all video data for cross-channel analysis ---
    all_videos = list(video_data)
    if competitors_data:
        for comp in competitors_data:
            for v in comp.get("videos", []):
                v_copy = dict(v)
                v_copy["_source"] = comp.get("name", "Unknown")
                v_copy["_category"] = comp.get("category", "Unknown")
                all_videos.append(v_copy)

    # --- Own channel stats ---
    own_avg_views = sum(v["view_count"] for v in video_data) // len(video_data)
    trends["own_avg_views"] = own_avg_views
    trends["own_video_count"] = len(video_data)

    # --- Title pattern analysis (cross-channel) ---
    patterns = {
        "How to / Tutorial": r"(?i)\b(how to|tutorial|guide|step.by.step|walkthrough|setup|install)\b",
        "Listicle": r"(?i)\b(top \d+|\d+ best|\d+ ways|\d+ things|\d+ reasons)\b",
        "Question": r"(?i)^(what|why|how|is |are |can |should |do |does |will )",
        "Review / First Look": r"(?i)\b(review|first look|first impressions|hands.on|unboxing)\b",
        "News / Update": r"(?i)\b(update|news|just released|announced|breaking|new )\b",
        "Comparison": r"(?i)\b(vs\.?|versus|compared|comparison|better than)\b",
    }

    overall_avg_views = sum(v["view_count"] for v in all_videos) // len(all_videos)
    trends["overall_avg_views"] = overall_avg_views
    trends["total_video_count"] = len(all_videos)

    pattern_stats = {}
    for label, regex in patterns.items():
        matching = [v for v in all_videos if re.search(regex, v["title"])]
        if matching:
            own_matching = [v for v in matching if "_source" not in v]
            comp_matching = [v for v in matching if "_source" in v]
            avg_views = sum(v["view_count"] for v in matching) // len(matching)
            avg_engagement = round(sum(v["engagement_rate"] for v in matching) / len(matching), 2)
            best = max(matching, key=lambda v: v["view_count"])
            pattern_stats[label] = {
                "count": len(matching),
                "own_count": len(own_matching),
                "comp_count": len(comp_matching),
                "avg_views": avg_views,
                "avg_engagement": avg_engagement,
                "best_title": best["title"],
                "best_views": best["view_count"],
                "best_source": best.get("_source", "You"),
            }

    trends["title_patterns"] = dict(
        sorted(pattern_stats.items(), key=lambda x: x[1]["avg_views"], reverse=True)
    )

    # --- Recent trends (last 90 days) ---
    cutoff_90 = datetime.now(timezone.utc) - timedelta(days=90)
    recent_all = []
    for v in all_videos:
        try:
            dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
            if dt >= cutoff_90:
                recent_all.append(v)
        except (ValueError, KeyError):
            pass

    if recent_all:
        recent_avg = sum(v["view_count"] for v in recent_all) // len(recent_all)

        # Group recent videos by source channel
        by_source = defaultdict(list)
        for v in recent_all:
            by_source[v.get("_source", "You")].append(v)

        # Find outliers per channel (2x that channel's own average)
        per_channel_outliers = {}
        per_channel_avg = {}
        for source, vids in by_source.items():
            ch_avg = sum(v["view_count"] for v in vids) // len(vids)
            per_channel_avg[source] = ch_avg
            outliers = [v for v in vids if v["view_count"] > ch_avg * 2]
            outliers.sort(key=lambda v: v["view_count"], reverse=True)
            if outliers:
                per_channel_outliers[source] = outliers

        # Round-robin interleave for even dispersion across channels
        recent_breakouts = []
        if per_channel_outliers:
            max_per = max(len(o) for o in per_channel_outliers.values())
            sources = sorted(per_channel_outliers.keys())
            for i in range(max_per):
                for src in sources:
                    if i < len(per_channel_outliers[src]):
                        recent_breakouts.append(per_channel_outliers[src][i])
                if len(recent_breakouts) >= 10:
                    break

        trends["recent_breakouts"] = [
            {
                "title": v["title"],
                "views": v["view_count"],
                "engagement": v["engagement_rate"],
                "source": v.get("_source", "You"),
                "published": v.get("published_at", "")[:10],
                "thumbnail_url": v.get("thumbnail_url", ""),
                "video_id": v.get("video_id", ""),
                "channel_id": v.get("_channel_id", ""),
                "source_category": v.get("_channel_category", "Bitcoin/Crypto"),
                "market_phase": v.get("_market_phase", "unknown"),
                "multiplier": round(v["view_count"] / max(per_channel_avg.get(v.get("_source", "You"), 1), 1), 1),
            }
            for v in recent_breakouts[:10]
        ]
        trends["recent_video_count"] = len(recent_all)
        trends["recent_avg_views"] = recent_avg

        # Recent title pattern trends
        recent_patterns = {}
        for label, regex in patterns.items():
            matching = [v for v in recent_all if re.search(regex, v["title"])]
            if matching:
                recent_patterns[label] = {
                    "count": len(matching),
                    "avg_views": sum(v["view_count"] for v in matching) // len(matching),
                }
        trends["recent_patterns"] = dict(
            sorted(recent_patterns.items(), key=lambda x: x[1]["avg_views"], reverse=True)
        )

    # --- Cross-channel topic clustering (keyword frequency) ---
    stop_words = {"the", "a", "an", "i", "my", "is", "it", "in", "to", "for", "of", "and",
                  "on", "with", "this", "that", "you", "your", "but", "not", "are", "was",
                  "be", "or", "at", "by", "we", "from", "so", "if", "do", "no", "just",
                  "its", "me", "has", "have", "had", "can", "will", "one", "all", "get",
                  "new", "been", "than", "up", "out", "about", "how", "what", "when", "why"}
    keyword_data = defaultdict(lambda: {"count": 0, "total_views": 0, "videos": []})
    for v in recent_all if recent_all else all_videos:
        words = re.findall(r"[a-zA-Z]+", v["title"].lower())
        seen = set()
        for w in words:
            if w not in stop_words and len(w) > 2 and w not in seen:
                seen.add(w)
                keyword_data[w]["count"] += 1
                keyword_data[w]["total_views"] += v["view_count"]
                keyword_data[w]["videos"].append(v["title"][:50])

    # Only keep keywords that appear in 3+ videos
    hot_keywords = {
        k: {
            "count": d["count"],
            "avg_views": d["total_views"] // d["count"],
            "sample_titles": d["videos"][:3],
        }
        for k, d in keyword_data.items()
        if d["count"] >= 3
    }
    trends["hot_keywords"] = dict(
        sorted(hot_keywords.items(), key=lambda x: x[1]["avg_views"], reverse=True)[:20]
    )

    # --- Bitcoin/Crypto specific keywords ---
    btc_terms = {
        "bitcoin", "btc", "wallet", "lightning", "node", "coldcard", "trezor",
        "ledger", "seed", "multisig", "nostr", "self-custody", "custody",
        "hardware", "privacy", "mining", "halving", "etf", "sats",
        "stacking", "hodl", "sovereign", "decentralized", "blockchain",
        "layer", "taproot", "segwit", "mempool", "utxo", "coinjoin",
        "whirlpool", "samourai", "sparrow", "electrum", "wasabi", "mutiny",
        "phoenix", "breez", "zeus", "umbrel", "start9", "mynode", "raspiblitz",
        "bitkey", "coldpower", "seedsigner", "jade", "passport", "foundation",
        "coinkite", "bull", "bear", "dca", "exchange", "kyc", "nokyc",
    }
    crypto_keyword_data = defaultdict(lambda: {"count": 0, "total_views": 0})
    source_videos = recent_all if recent_all else all_videos
    for v in source_videos:
        title_lower = v["title"].lower()
        words = set(re.findall(r"[a-zA-Z0-9-]+", title_lower))
        for w in words:
            if w in btc_terms:
                crypto_keyword_data[w]["count"] += 1
                crypto_keyword_data[w]["total_views"] += v["view_count"]
        # Multi-word matches
        for phrase in ["self custody", "self-custody", "hardware wallet", "lightning network",
                       "full node", "cold storage", "seed phrase", "private key"]:
            if phrase in title_lower:
                crypto_keyword_data[phrase]["count"] += 1
                crypto_keyword_data[phrase]["total_views"] += v["view_count"]

    crypto_keywords = {
        k: {"count": d["count"], "avg_views": d["total_views"] // d["count"]}
        for k, d in crypto_keyword_data.items()
        if d["count"] >= 2
    }
    trends["crypto_keywords"] = dict(
        sorted(crypto_keywords.items(), key=lambda x: x[1]["avg_views"], reverse=True)[:15]
    )

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
        day_performance[day] = {"count": len(vids), "avg_views": avg_views}
    trends["publish_days"] = dict(
        sorted(day_performance.items(), key=lambda x: x[1]["avg_views"], reverse=True)
    )

    # --- Monthly performance ---
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

    # --- Hidden gems ---
    median_views = sorted(v["view_count"] for v in video_data)[len(video_data) // 2]
    avg_eng = sum(v["engagement_rate"] for v in video_data) / len(video_data)
    hidden_gems = [
        v for v in video_data
        if v["engagement_rate"] > avg_eng * 1.5 and v["view_count"] < median_views
    ]
    hidden_gems.sort(key=lambda v: v["engagement_rate"], reverse=True)
    trends["hidden_gems"] = [
        {"title": v["title"], "views": v["view_count"], "engagement": v["engagement_rate"]}
        for v in hidden_gems[:10]
    ]

    # --- Breakout hits ---
    breakout_threshold = own_avg_views * 2
    breakouts = [v for v in video_data if v["view_count"] > breakout_threshold]
    breakouts.sort(key=lambda v: v["view_count"], reverse=True)
    trends["breakout_hits"] = [
        {
            "title": v["title"],
            "views": v["view_count"],
            "multiplier": round(v["view_count"] / own_avg_views, 1),
            "engagement": v["engagement_rate"],
            "thumbnail_url": v.get("thumbnail_url", ""),
            "video_id": v.get("video_id", ""),
            "market_phase": v.get("_market_phase", "unknown"),
        }
        for v in breakouts[:10]
    ]

    # --- Title length analysis ---
    short_titles = [v for v in all_videos if len(v["title"]) <= 40]
    medium_titles = [v for v in all_videos if 40 < len(v["title"]) <= 70]
    long_titles = [v for v in all_videos if len(v["title"]) > 70]

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

    # --- Competitor comparison summary ---
    if competitors_data:
        comp_summary = []
        for comp in competitors_data:
            vids = comp.get("videos", [])
            if not vids:
                continue
            comp_avg = sum(v["view_count"] for v in vids) // len(vids)
            comp_eng = round(sum(v["engagement_rate"] for v in vids) / len(vids), 2)
            recent_comp = [v for v in vids if _parse_date(v) and _parse_date(v) >= cutoff_90]
            comp_summary.append({
                "name": comp.get("name", "Unknown"),
                "category": comp.get("category", "Unknown"),
                "total_videos": len(vids),
                "avg_views": comp_avg,
                "avg_engagement": comp_eng,
                "recent_count": len(recent_comp),
            })
        comp_summary.sort(key=lambda c: c["avg_views"], reverse=True)
        trends["competitor_summary"] = comp_summary

    return trends


def _parse_date(v):
    try:
        return datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
    except (ValueError, KeyError):
        return None


def trends_to_prompt_section(trends: dict) -> str:
    """Convert trend analysis into a prompt section for the AI."""
    if not trends:
        return ""

    sections = ["\n**Content Trend Analysis:**"]

    tp = trends.get("title_patterns", {})
    overall_avg = trends.get("overall_avg_views", 0)
    if tp:
        sections.append("\nTitle Format Performance (cross-channel avg: {:,}):".format(overall_avg))
        for label, stats in tp.items():
            diff = stats["avg_views"] - overall_avg
            direction = "above" if diff > 0 else "below"
            sections.append(
                f"  - {label}: {stats['avg_views']:,} avg views ({abs(diff):,} {direction} avg), "
                f"{stats['count']} videos, {stats['avg_engagement']}% engagement"
            )

    days = trends.get("publish_days", {})
    if days:
        best_days = list(days.items())[:3]
        sections.append("\nBest Publishing Days:")
        for day, stats in best_days:
            sections.append(f"  - {day}: {stats['avg_views']:,} avg views ({stats['count']} videos)")

    tl = trends.get("title_length", {})
    if tl:
        sections.append("\nTitle Length Impact:")
        for label, stats in sorted(tl.items(), key=lambda x: x[1]["avg_views"], reverse=True):
            sections.append(f"  - {label}: {stats['avg_views']:,} avg views ({stats['count']} videos)")

    breakouts = trends.get("breakout_hits", [])
    if breakouts:
        sections.append("\nBreakout Hits (2x+ above average):")
        for b in breakouts[:5]:
            sections.append(
                f"  - \"{b['title'][:55]}\" — {b['views']:,} views ({b['multiplier']}x avg)"
            )

    gems = trends.get("hidden_gems", [])
    if gems:
        sections.append("\nHidden Gems (high engagement, below-median views):")
        for g in gems[:5]:
            sections.append(
                f"  - \"{g['title'][:55]}\" — {g['views']:,} views, {g['engagement']}% engagement"
            )

    freq = trends.get("avg_uploads_per_month")
    if freq:
        sections.append(f"\nUpload Frequency: {freq} videos/month average")

    return "\n".join(sections)
