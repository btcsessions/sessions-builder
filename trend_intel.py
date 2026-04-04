"""Real-time trend intelligence for bitcoin/crypto and general tech.

Aggregates signals from free public APIs and caches results.
Used to enrich AI prompts with current context.
"""

import json
import os
import time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
INTEL_CACHE_FILE = os.path.join(DATA_DIR, "trend_intel.json")
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


# --- Bitcoin / Crypto Signals ---

def _fetch_coingecko_trending() -> list[dict]:
    """Get trending coins from CoinGecko (free, no key)."""
    data = _api_get("https://api.coingecko.com/api/v3/search/trending")
    if not data:
        return []
    coins = []
    for item in data.get("coins", [])[:10]:
        c = item.get("item", {})
        coins.append({
            "name": c.get("name", ""),
            "symbol": c.get("symbol", ""),
            "market_cap_rank": c.get("market_cap_rank", 0),
        })
    # Also grab trending categories if available
    categories = []
    for cat in data.get("categories", [])[:5]:
        categories.append(cat.get("name", ""))
    return coins, categories


def _fetch_coingecko_global() -> dict:
    """Get global crypto market data."""
    data = _api_get("https://api.coingecko.com/api/v3/global")
    if not data or "data" not in data:
        return {}
    g = data["data"]
    return {
        "total_market_cap_usd": round(g.get("total_market_cap", {}).get("usd", 0)),
        "market_cap_change_24h": round(g.get("market_cap_change_percentage_24h_usd", 0), 1),
        "btc_dominance": round(g.get("market_cap_percentage", {}).get("btc", 0), 1),
        "active_cryptocurrencies": g.get("active_cryptocurrencies", 0),
    }


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


def _fetch_nostr_trending() -> list[str]:
    """Get trending hashtags/topics from nostr.band (freedom tech signal)."""
    data = _api_get("https://api.nostr.band/v0/trending/hashtags")
    if not data:
        return []
    tags = []
    for item in data.get("hashtags", [])[:15]:
        tag = item.get("hashtag", "")
        if tag:
            tags.append(tag)
    return tags


# --- General Tech Signals ---

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
    from datetime import timedelta
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


def _fetch_producthunt_trending() -> list[dict]:
    """Approximate tech product trends via recent popular launches."""
    # ProductHunt doesn't have a free public API, skip for now
    return []


# --- Aggregation ---

def gather_trend_intel() -> dict:
    """Gather all trend signals. Returns structured intel dict."""
    print("[TrendIntel] Gathering fresh trend intelligence...")

    # Check cache first
    cached = _load_cache()
    if cached:
        return cached

    intel = {
        "_fetched_at": datetime.now(timezone.utc).isoformat(),
        "crypto": {},
        "bitcoin_network": {},
        "tech": {},
    }

    # Crypto trends
    try:
        trending_coins, trending_categories = _fetch_coingecko_trending()
        intel["crypto"]["trending_coins"] = trending_coins
        intel["crypto"]["trending_categories"] = trending_categories
    except Exception as e:
        print(f"[TrendIntel] Trending coins error: {e}")
        intel["crypto"]["trending_coins"] = []
        intel["crypto"]["trending_categories"] = []

    intel["crypto"]["global_market"] = _fetch_coingecko_global()

    # Bitcoin network state
    intel["bitcoin_network"]["fees"] = _fetch_mempool_fees()
    intel["bitcoin_network"]["mempool"] = _fetch_mempool_stats()
    intel["bitcoin_network"]["block_height"] = _fetch_bitcoin_block_height()

    # Freedom tech / nostr
    intel["crypto"]["nostr_trending"] = _fetch_nostr_trending()

    # General tech
    intel["tech"]["hackernews_top"] = _fetch_hackernews_top()
    intel["tech"]["github_trending"] = _fetch_github_trending_topics()

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

    # Crypto market
    gm = intel.get("crypto", {}).get("global_market", {})
    if gm:
        lines.append(f"\nCrypto Market:")
        if gm.get("total_market_cap_usd"):
            lines.append(f"- Total market cap: ${gm['total_market_cap_usd']:,}")
        if gm.get("market_cap_change_24h"):
            lines.append(f"- 24h change: {gm['market_cap_change_24h']}%")
        if gm.get("btc_dominance"):
            lines.append(f"- BTC dominance: {gm['btc_dominance']}%")

    # Trending coins
    coins = intel.get("crypto", {}).get("trending_coins", [])
    if coins:
        coin_names = [f"{c['name']} ({c['symbol']})" for c in coins[:8]]
        lines.append(f"\nTrending Coins: {', '.join(coin_names)}")

    # Trending crypto categories
    cats = intel.get("crypto", {}).get("trending_categories", [])
    if cats:
        lines.append(f"Trending Crypto Categories: {', '.join(cats[:5])}")

    # Bitcoin network
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

    # Nostr / freedom tech
    nostr = intel.get("crypto", {}).get("nostr_trending", [])
    if nostr:
        lines.append(f"\nNostr/Freedom Tech Trending: #{', #'.join(nostr[:10])}")

    # Tech trends
    hn = intel.get("tech", {}).get("hackernews_top", [])
    if hn:
        lines.append(f"\nTech Pulse (Hacker News top stories):")
        for story in hn[:10]:
            lines.append(f"- {story['title']} (score: {story['score']})")

    gh = intel.get("tech", {}).get("github_trending", [])
    if gh:
        lines.append(f"\nGitHub Trending Repos:")
        for repo in gh[:6]:
            desc = f" — {repo['description']}" if repo['description'] else ""
            lines.append(f"- {repo['name']} ({repo['stars']} stars){desc}")

    lines.append("")
    lines.append("Use these real-time signals to inform your suggestions — reference specific trending topics, network conditions, or tech trends where relevant.")

    return "\n".join(lines)
