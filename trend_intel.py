"""Real-time trend intelligence for bitcoin/crypto and general tech.

Aggregates signals from free public APIs and caches results.
Used to enrich AI prompts with current context.

Sources:
  Bitcoin/Crypto:
    - mempool.space: Bitcoin fees, mempool size, block height, hashrate
    - Nostr.band: trending hashtags, trending notes, trending profiles
    - Stacker.news: top bitcoin community discussions (GraphQL)

  General Tech:
    - Hacker News: top tech stories
    - GitHub: trending repos from the past week
    - TechCrunch, Wired, TechRadar: latest headlines via RSS
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
INTEL_CACHE_FILE = os.path.join(DATA_DIR, "trend_intel.json")
NOSTR_HISTORY_FILE = os.path.join(DATA_DIR, "nostr_history.json")
INTEL_CACHE_TTL = 4 * 3600  # 4 hours


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _api_get(url: str, timeout: int = 15) -> dict | list | None:
    """Simple GET request returning parsed JSON."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[TrendIntel] API call failed ({url[:60]}...): {e}")
        return None


def _fetch_rss(url: str, timeout: int = 15) -> str | None:
    """Fetch RSS/XML feed and return raw text."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml",
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[TrendIntel] RSS fetch failed ({url[:50]}...): {e}")
        return None


def _parse_rss_items(xml_text: str, max_items: int = 12) -> list[dict]:
    """Parse RSS 2.0 feed and return list of {title, link, description}."""
    try:
        root = ET.fromstring(xml_text)
        items = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            # Clean HTML from description
            desc_raw = (item.findtext("description") or "").strip()
            # Strip HTML tags for a clean summary
            desc = _strip_html(desc_raw)[:200]
            categories = [c.text for c in item.findall("category") if c.text]
            if title:
                items.append({
                    "title": title,
                    "link": link,
                    "description": desc,
                    "categories": categories[:3],
                })
            if len(items) >= max_items:
                break
        return items
    except ET.ParseError as e:
        print(f"[TrendIntel] RSS parse error: {e}")
        return []


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


# =========================================================================
#  Bitcoin / Crypto Signals
# =========================================================================

def _fetch_mempool_fees() -> dict:
    """Get current Bitcoin mempool/fee data from mempool.space."""
    data = _api_get("https://mempool.space/api/v1/fees/recommended")
    if not data:
        return {}
    return {
        "fastest_fee": data.get("fastestFee", 0),
        "half_hour_fee": data.get("halfHourFee", 0),
        "hour_fee": data.get("hourFee", 0),
        "economy_fee": data.get("economyFee", 0),
    }


def _fetch_mempool_stats() -> dict:
    """Get mempool size stats."""
    data = _api_get("https://mempool.space/api/mempool")
    if not data:
        return {}
    return {
        "tx_count": data.get("count", 0),
        "vsize_mb": round(data.get("vsize", 0) / 1_000_000, 1),
    }


def _fetch_bitcoin_block_height() -> int:
    """Get current block height."""
    try:
        req = Request("https://mempool.space/api/blocks/tip/height",
                      headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:
            return int(resp.read().decode().strip())
    except Exception:
        return 0


def _fetch_mempool_hashrate() -> dict:
    """Get recent hashrate info from mempool.space."""
    data = _api_get("https://mempool.space/api/v1/mining/hashrate/1w")
    if not data:
        return {}
    hashrates = data.get("hashrates", [])
    if hashrates:
        latest = hashrates[-1]
        return {
            "hashrate_eh": round(latest.get("avgHashrate", 0) / 1e18, 1),
        }
    return {}


def _fetch_bitcoin_difficulty() -> dict:
    """Get difficulty adjustment info."""
    data = _api_get("https://mempool.space/api/v1/difficulty-adjustment")
    if not data:
        return {}
    return {
        "progress_pct": round(data.get("progressPercent", 0), 1),
        "estimated_change_pct": round(data.get("difficultyChange", 0), 1),
        "remaining_blocks": data.get("remainingBlocks", 0),
    }


# --- Nostr / Freedom Tech ---

def _fetch_nostr_trending_hashtags() -> list[str]:
    """Get trending hashtags from nostr.band."""
    data = _api_get("https://api.nostr.band/v0/trending/hashtags")
    if not data:
        return []
    tags = []
    for item in data.get("hashtags", [])[:15]:
        tag = item.get("hashtag", "")
        if tag:
            tags.append(tag)
    return tags


def _fetch_nostr_trending_notes() -> list[dict]:
    """Get trending notes (posts) from nostr.band — what the community is discussing."""
    data = _api_get("https://api.nostr.band/v0/trending/notes")
    if not data:
        return []
    notes = []
    for item in data.get("notes", [])[:10]:
        event = item.get("event", {})
        content = (event.get("content") or "")[:200]
        author = item.get("author", {})
        name = author.get("name") or author.get("display_name") or ""
        if content:
            notes.append({
                "content": content,
                "author": name,
            })
    return notes


def _fetch_nostr_trending_profiles() -> list[dict]:
    """Get trending profiles on nostr — who's gaining attention."""
    data = _api_get("https://api.nostr.band/v0/trending/profiles")
    if not data:
        return []
    profiles = []
    for item in data.get("profiles", [])[:8]:
        profile = item.get("profile", {})
        name = profile.get("name") or profile.get("display_name") or ""
        about = (profile.get("about") or "")[:100]
        nip05 = profile.get("nip05", "")
        if name:
            profiles.append({
                "name": name,
                "about": about,
                "nip05": nip05,
            })
    return profiles


# --- Nostr 28-Day Rolling History ---

def _load_nostr_history() -> dict:
    """Load the rolling nostr trend history."""
    if os.path.exists(NOSTR_HISTORY_FILE):
        try:
            with open(NOSTR_HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"days": {}}


def _save_nostr_history(history: dict):
    """Save nostr trend history."""
    _ensure_data_dir()
    try:
        with open(NOSTR_HISTORY_FILE, "w") as f:
            json.dump(history, f)
    except IOError as e:
        print(f"[TrendIntel] Nostr history save failed: {e}")


def record_nostr_snapshot(hashtags: list[str], notes: list[dict], profiles: list[dict]):
    """Record today's nostr trending data into the rolling 28-day history."""
    history = _load_nostr_history()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Don't overwrite if we already have today's snapshot
    if today in history["days"]:
        return history

    history["days"][today] = {
        "hashtags": hashtags[:15],
        "note_topics": [n.get("content", "")[:150] for n in notes[:10]],
        "profiles": [p.get("name", "") for p in profiles[:8]],
    }

    # Prune entries older than 28 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=28)).strftime("%Y-%m-%d")
    history["days"] = {d: v for d, v in history["days"].items() if d >= cutoff}

    _save_nostr_history(history)
    print(f"[TrendIntel] Nostr snapshot recorded for {today} ({len(history['days'])} days in history)")
    return history


def analyze_nostr_history() -> dict:
    """Analyze 28-day nostr history to find persistent trends and recurring topics."""
    history = _load_nostr_history()
    days = history.get("days", {})

    if not days:
        return {}

    # Count hashtag frequency across days
    hashtag_counts = {}
    for day_data in days.values():
        for tag in day_data.get("hashtags", []):
            tag_lower = tag.lower()
            hashtag_counts[tag_lower] = hashtag_counts.get(tag_lower, 0) + 1

    # Sort by frequency (topics that trend repeatedly are more significant)
    recurring_hashtags = sorted(hashtag_counts.items(), key=lambda x: x[1], reverse=True)

    # Count profile mentions across days
    profile_counts = {}
    for day_data in days.values():
        for name in day_data.get("profiles", []):
            if name:
                profile_counts[name] = profile_counts.get(name, 0) + 1

    recurring_profiles = sorted(profile_counts.items(), key=lambda x: x[1], reverse=True)

    # Collect all note snippets for topic extraction
    all_notes = []
    for date_str in sorted(days.keys(), reverse=True):
        for note in days[date_str].get("note_topics", []):
            if note:
                all_notes.append(note)

    return {
        "days_tracked": len(days),
        "recurring_hashtags": [
            {"tag": tag, "days_seen": count}
            for tag, count in recurring_hashtags[:20]
        ],
        "recurring_profiles": [
            {"name": name, "days_seen": count}
            for name, count in recurring_profiles[:10]
        ],
        "recent_discussions": all_notes[:15],
    }


# --- Stacker News (Bitcoin community) ---

def _fetch_stacker_news_top() -> list[dict]:
    """Get top posts from stacker.news via GraphQL API."""
    query = {
        "query": """
            query TopItems($sort: String, $when: String, $limit: Limit) {
                items(sort: $sort, when: $when, limit: $limit) {
                    items {
                        title
                        url
                        sats
                        ncomments
                        user {
                            name
                        }
                    }
                }
            }
        """,
        "variables": {
            "sort": "top",
            "when": "week",
            "limit": 12,
        },
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        import json as _json
        body = _json.dumps(query).encode("utf-8")
        req = Request("https://stacker.news/api/graphql", data=body, headers=headers, method="POST")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("data", {}).get("items", {}).get("items", [])
        posts = []
        for item in items:
            title = item.get("title", "")
            if title:
                posts.append({
                    "title": title,
                    "sats": item.get("sats", 0),
                    "comments": item.get("ncomments", 0),
                    "author": (item.get("user") or {}).get("name", ""),
                })
        return posts
    except Exception as e:
        print(f"[TrendIntel] Stacker.news failed: {e}")
        return []


# =========================================================================
#  General Tech Signals
# =========================================================================

def _fetch_hackernews_top() -> list[dict]:
    """Get top stories from Hacker News (tech pulse)."""
    ids = _api_get("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not ids:
        return []
    stories = []
    for story_id in ids[:20]:
        item = _api_get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
        if item and item.get("title"):
            stories.append({
                "title": item["title"],
                "score": item.get("score", 0),
                "url": item.get("url", ""),
            })
    return stories


def _fetch_github_trending_topics() -> list[dict]:
    """Get trending repos from GitHub API (tech trends signal)."""
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    data = _api_get(
        f"https://api.github.com/search/repositories?q=created:>{week_ago}&sort=stars&order=desc&per_page=10"
    )
    if not data:
        return []
    repos = []
    for r in data.get("items", [])[:10]:
        repos.append({
            "name": r.get("full_name", ""),
            "description": (r.get("description") or "")[:100],
            "stars": r.get("stargazers_count", 0),
            "language": r.get("language", ""),
        })
    return repos


def _fetch_techcrunch_headlines() -> list[dict]:
    """Get latest TechCrunch headlines via RSS."""
    xml = _fetch_rss("https://techcrunch.com/feed/")
    if not xml:
        return []
    return _parse_rss_items(xml, max_items=10)


def _fetch_wired_headlines() -> list[dict]:
    """Get latest Wired headlines via RSS."""
    xml = _fetch_rss("https://www.wired.com/feed/rss")
    if not xml:
        return []
    return _parse_rss_items(xml, max_items=10)


def _fetch_techradar_headlines() -> list[dict]:
    """Get latest TechRadar headlines via RSS."""
    xml = _fetch_rss("https://www.techradar.com/rss")
    if not xml:
        return []
    return _parse_rss_items(xml, max_items=10)


# =========================================================================
#  Aggregation
# =========================================================================

def gather_trend_intel() -> dict:
    """Gather all trend signals. Returns structured intel dict."""
    # Check cache first
    cached = _load_cache()
    if cached:
        return cached

    print("[TrendIntel] Gathering fresh trend intelligence...")

    intel = {
        "_fetched_at": datetime.now(timezone.utc).isoformat(),
        "crypto": {},
        "bitcoin_network": {},
        "nostr": {},
        "stacker_news": {},
        "tech": {},
    }

    # --- Bitcoin network state ---
    intel["bitcoin_network"]["fees"] = _fetch_mempool_fees()
    intel["bitcoin_network"]["mempool"] = _fetch_mempool_stats()
    intel["bitcoin_network"]["block_height"] = _fetch_bitcoin_block_height()
    intel["bitcoin_network"]["hashrate"] = _fetch_mempool_hashrate()
    intel["bitcoin_network"]["difficulty"] = _fetch_bitcoin_difficulty()

    # --- Nostr / freedom tech ---
    nostr_hashtags = _fetch_nostr_trending_hashtags()
    nostr_notes = _fetch_nostr_trending_notes()
    nostr_profiles = _fetch_nostr_trending_profiles()
    intel["nostr"]["trending_hashtags"] = nostr_hashtags
    intel["nostr"]["trending_notes"] = nostr_notes
    intel["nostr"]["trending_profiles"] = nostr_profiles

    # Record daily snapshot and analyze 28-day rolling history
    record_nostr_snapshot(nostr_hashtags, nostr_notes, nostr_profiles)
    intel["nostr"]["history_28d"] = analyze_nostr_history()

    # --- Stacker News ---
    intel["stacker_news"]["top_posts"] = _fetch_stacker_news_top()

    # --- General tech ---
    intel["tech"]["hackernews_top"] = _fetch_hackernews_top()
    intel["tech"]["github_trending"] = _fetch_github_trending_topics()
    intel["tech"]["techcrunch"] = _fetch_techcrunch_headlines()
    intel["tech"]["wired"] = _fetch_wired_headlines()
    intel["tech"]["techradar"] = _fetch_techradar_headlines()

    # Save cache
    _save_cache(intel)
    print("[TrendIntel] Trend intelligence gathered and cached.")

    return intel


def _load_cache() -> dict | None:
    """Load cached intel if fresh enough."""
    if not os.path.exists(INTEL_CACHE_FILE):
        return None
    try:
        with open(INTEL_CACHE_FILE) as f:
            data = json.load(f)
        fetched = data.get("_fetched_at", "")
        if fetched:
            dt = datetime.fromisoformat(fetched)
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            if age < INTEL_CACHE_TTL:
                print(f"[TrendIntel] Using cached intel ({age/3600:.1f}h old)")
                return data
    except (json.JSONDecodeError, IOError, ValueError):
        pass
    return None


def _save_cache(intel: dict):
    """Save intel to cache file."""
    _ensure_data_dir()
    try:
        with open(INTEL_CACHE_FILE, "w") as f:
            json.dump(intel, f, indent=2)
    except IOError as e:
        print(f"[TrendIntel] Cache save failed: {e}")


def intel_to_prompt_context(intel: dict) -> str:
    """Convert trend intel dict into a text block for AI prompts."""
    if not intel:
        return ""

    lines = ["\n**CURRENT TREND INTELLIGENCE** (live data):"]

    # --- Bitcoin network ---
    net = intel.get("bitcoin_network", {})
    fees = net.get("fees", {})
    if fees:
        lines.append(f"\nBitcoin Network:")
        lines.append(f"- Fees: {fees.get('fastest_fee', '?')} sat/vB (fast), {fees.get('hour_fee', '?')} sat/vB (1hr)")
        mempool = net.get("mempool", {})
        if mempool.get("tx_count"):
            lines.append(f"- Mempool: {mempool['tx_count']:,} txs ({mempool.get('vsize_mb', 0)} MB)")
        bh = net.get("block_height", 0)
        if bh:
            lines.append(f"- Block height: {bh:,}")
        hr = net.get("hashrate", {})
        if hr.get("hashrate_eh"):
            lines.append(f"- Hashrate: {hr['hashrate_eh']} EH/s")
        diff = net.get("difficulty", {})
        if diff.get("remaining_blocks"):
            lines.append(f"- Difficulty adjustment: {diff['progress_pct']}% through epoch, est. {diff['estimated_change_pct']}% change, {diff['remaining_blocks']} blocks remaining")

    # --- Nostr / freedom tech ---
    nostr = intel.get("nostr", {})
    nostr_tags = nostr.get("trending_hashtags", [])
    if nostr_tags:
        lines.append(f"\nNostr/Freedom Tech Trending Hashtags: #{', #'.join(nostr_tags[:10])}")

    nostr_notes = nostr.get("trending_notes", [])
    if nostr_notes:
        lines.append(f"\nNostr Hot Discussions:")
        for note in nostr_notes[:6]:
            author = f" — {note['author']}" if note.get("author") else ""
            content = note["content"][:120]
            lines.append(f"- \"{content}\"{author}")

    nostr_profiles = nostr.get("trending_profiles", [])
    if nostr_profiles:
        names = [p["name"] for p in nostr_profiles[:6]]
        lines.append(f"\nNostr Trending Profiles: {', '.join(names)}")

    # Nostr 28-day rolling trends
    nostr_history = nostr.get("history_28d", {})
    if nostr_history.get("days_tracked", 0) > 1:
        days_tracked = nostr_history["days_tracked"]
        lines.append(f"\nNostr 28-Day Trend Analysis ({days_tracked} days tracked):")

        recurring = nostr_history.get("recurring_hashtags", [])
        if recurring:
            # Show tags that appeared on multiple days — these are persistent trends
            persistent = [f"#{h['tag']} ({h['days_seen']}d)" for h in recurring if h["days_seen"] > 1]
            if persistent:
                lines.append(f"Persistent topics (appeared multiple days): {', '.join(persistent[:12])}")
            # Also show recent one-offs that might be emerging
            emerging = [f"#{h['tag']}" for h in recurring if h["days_seen"] == 1][:8]
            if emerging:
                lines.append(f"Emerging/new topics (last seen once): {', '.join(emerging)}")

        recurring_profiles = nostr_history.get("recurring_profiles", [])
        if recurring_profiles:
            frequent = [f"{p['name']} ({p['days_seen']}d)" for p in recurring_profiles if p["days_seen"] > 1][:8]
            if frequent:
                lines.append(f"Consistently trending voices: {', '.join(frequent)}")

        recent_notes = nostr_history.get("recent_discussions", [])
        if recent_notes:
            lines.append(f"Recent community discussions (last 28 days):")
            for note in recent_notes[:6]:
                lines.append(f"- \"{note[:120]}\"")

    # --- Stacker News ---
    sn = intel.get("stacker_news", {}).get("top_posts", [])
    if sn:
        lines.append(f"\nStacker News Top Posts (Bitcoin community):")
        for post in sn[:8]:
            sats = f" ({post['sats']:,} sats)" if post.get("sats") else ""
            lines.append(f"- {post['title']}{sats}")

    # --- Tech trends ---
    hn = intel.get("tech", {}).get("hackernews_top", [])
    if hn:
        lines.append(f"\nTech Pulse (Hacker News):")
        for story in hn[:10]:
            lines.append(f"- {story['title']} (score: {story['score']})")

    gh = intel.get("tech", {}).get("github_trending", [])
    if gh:
        lines.append(f"\nGitHub Trending Repos:")
        for repo in gh[:6]:
            desc = f" — {repo['description']}" if repo['description'] else ""
            lines.append(f"- {repo['name']} ({repo['stars']} stars){desc}")

    # Tech publication headlines
    for source_key, label in [("techcrunch", "TechCrunch"), ("wired", "Wired"), ("techradar", "TechRadar")]:
        articles = intel.get("tech", {}).get(source_key, [])
        if articles:
            titles = [a["title"] for a in articles[:6]]
            lines.append(f"\n{label} Headlines:")
            for t in titles:
                lines.append(f"- {t}")

    lines.append("")
    lines.append("Use these real-time signals to inform your suggestions — reference specific trending topics, network conditions, community discussions, or tech trends where relevant. Lean into what's hot RIGHT NOW.")

    return "\n".join(lines)
