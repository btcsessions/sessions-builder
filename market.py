"""Bitcoin market data, sentiment classification, and video longevity scoring."""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BTC_CACHE_FILE = os.path.join(DATA_DIR, "btc_prices.json")
SNAPSHOTS_FILE = os.path.join(DATA_DIR, "snapshots.json")

# Cache BTC prices for 6 hours
BTC_CACHE_TTL = 6 * 3600


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def fetch_btc_prices(days: int = 730) -> dict:
    """Fetch daily BTC prices from CoinGecko. Returns {date_str: price}."""
    # Check cache
    if os.path.exists(BTC_CACHE_FILE):
        with open(BTC_CACHE_FILE) as f:
            cached = json.load(f)
        if time.time() - cached.get("_fetched_at", 0) < BTC_CACHE_TTL:
            return cached.get("prices", {})

    url = f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days={days}&interval=daily"
    req = Request(url, headers={"User-Agent": "YT-Planner/1.0"})

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (URLError, Exception):
        # Return cached data if available, even if stale
        if os.path.exists(BTC_CACHE_FILE):
            with open(BTC_CACHE_FILE) as f:
                return json.load(f).get("prices", {})
        return {}

    prices = {}
    for timestamp_ms, price in data.get("prices", []):
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        prices[date_str] = round(price, 2)

    # Save cache
    _ensure_data_dir()
    with open(BTC_CACHE_FILE, "w") as f:
        json.dump({"_fetched_at": time.time(), "prices": prices}, f)

    return prices


def compute_200d_ma(prices: dict, date_str: str) -> float | None:
    """Compute 200-day moving average for a given date."""
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None

    values = []
    for i in range(200):
        d = (target - timedelta(days=i)).strftime("%Y-%m-%d")
        if d in prices:
            values.append(prices[d])

    if len(values) < 100:  # Need at least 100 days of data
        return None

    return round(sum(values) / len(values), 2)


def classify_market_phase(price: float, ma_200: float | None) -> str:
    """Classify market phase based on price vs 200-day MA."""
    if ma_200 is None:
        return "unknown"
    ratio = price / ma_200
    if ratio >= 1.15:
        return "bull"
    elif ratio <= 0.85:
        return "bear"
    else:
        return "sideways"


def get_current_market_info(prices: dict) -> dict:
    """Get current BTC price and market phase."""
    if not prices:
        return {"price": 0, "phase": "unknown", "ma_200": 0}

    # Get most recent price
    sorted_dates = sorted(prices.keys(), reverse=True)
    if not sorted_dates:
        return {"price": 0, "phase": "unknown", "ma_200": 0}

    current_date = sorted_dates[0]
    current_price = prices[current_date]
    ma_200 = compute_200d_ma(prices, current_date)
    phase = classify_market_phase(current_price, ma_200)

    return {
        "price": current_price,
        "phase": phase,
        "ma_200": ma_200 or 0,
        "date": current_date,
    }


def tag_video_market_phase(video: dict, prices: dict) -> dict:
    """Add market phase info to a video dict based on its publish date."""
    try:
        pub_date = video["published_at"][:10]  # YYYY-MM-DD
    except (KeyError, TypeError):
        video["_market_phase"] = "unknown"
        video["_btc_price_at_publish"] = 0
        return video

    # Find closest price to publish date
    price = prices.get(pub_date)
    if not price:
        # Try adjacent days
        for offset in range(1, 5):
            dt = datetime.strptime(pub_date, "%Y-%m-%d")
            for d in [(dt - timedelta(days=offset)).strftime("%Y-%m-%d"),
                       (dt + timedelta(days=offset)).strftime("%Y-%m-%d")]:
                if d in prices:
                    price = prices[d]
                    break
            if price:
                break

    if not price:
        video["_market_phase"] = "unknown"
        video["_btc_price_at_publish"] = 0
        return video

    ma_200 = compute_200d_ma(prices, pub_date)
    video["_market_phase"] = classify_market_phase(price, ma_200)
    video["_btc_price_at_publish"] = price
    return video


def compute_longevity_score(video: dict) -> dict:
    """Compute longevity metrics for a video."""
    try:
        pub_date = datetime.fromisoformat(video["published_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        video["_days_old"] = 0
        video["_views_per_day"] = 0
        video["_longevity_score"] = 0
        return video

    days_old = max((datetime.now(timezone.utc) - pub_date).days, 1)
    views = video.get("view_count", 0)
    views_per_day = round(views / days_old, 1)

    # Expected decay: a typical YouTube video gets ~60% of lifetime views
    # in the first 30 days. After that, views/day decays roughly as 1/sqrt(days).
    # Expected views/day at age D (normalized to day-1 performance):
    # We use a simple model: expected_vpd_ratio = 30 / (days_old + 29)
    # This means at day 1, ratio = 1.0; at day 30, ratio = 0.51; at day 365, ratio = 0.076
    if days_old <= 7:
        # Too new to score longevity
        longevity = 0
    else:
        # Compare actual vpd to what we'd expect for a video this old
        # Normalize: if every video had the same total views, what would vpd look like at this age?
        # We just compare vpd relative to total views and age
        # Higher = still getting views, lower = front-loaded
        expected_vpd = views * (30 / (days_old + 29)) / days_old
        if expected_vpd > 0:
            longevity = round(views_per_day / (expected_vpd * days_old / 30 * 30 / (days_old + 29)), 1)
        else:
            longevity = 0

        # Simplified: use a cleaner formula
        # longevity = (views_per_day * days_old) / views if views > 0
        # This equals 1.0 always (it's just vpd * days / views = 1)
        # Better approach: compare recent velocity to overall velocity
        # Without snapshots, use: vpd * sqrt(days_old) / baseline_vpd
        # For now, just use views_per_day as the raw metric and let
        # the snapshot system handle true longevity over time
        longevity = 0  # Will be set properly once we have snapshots

    video["_days_old"] = days_old
    video["_views_per_day"] = views_per_day
    video["_longevity_score"] = longevity
    return video


# --- Snapshot tracking ---

def load_snapshots() -> dict:
    """Load historical view count snapshots. Format: {video_id: [{date, views}, ...]}"""
    if os.path.exists(SNAPSHOTS_FILE):
        with open(SNAPSHOTS_FILE) as f:
            return json.load(f)
    return {}


def save_snapshots(snapshots: dict):
    _ensure_data_dir()
    with open(SNAPSHOTS_FILE, "w") as f:
        json.dump(snapshots, f)


def record_snapshot(video_cache: list, competitors_cache: list):
    """Record current view counts for all videos. Called on each app launch."""
    snapshots = load_snapshots()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    all_videos = list(video_cache)
    for comp in competitors_cache:
        all_videos.extend(comp.get("videos", []))

    for v in all_videos:
        vid = v.get("video_id")
        if not vid:
            continue

        if vid not in snapshots:
            snapshots[vid] = []

        # Don't record more than once per day
        existing_dates = {s["date"] for s in snapshots[vid]}
        if today not in existing_dates:
            snapshots[vid].append({
                "date": today,
                "views": v.get("view_count", 0),
            })

        # Keep only last 90 days of snapshots
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        snapshots[vid] = [s for s in snapshots[vid] if s["date"] >= cutoff]

    save_snapshots(snapshots)
    return snapshots


def compute_velocity(video_id: str, snapshots: dict) -> dict:
    """Compute view velocity from snapshots for a video."""
    history = snapshots.get(video_id, [])
    if len(history) < 2:
        return {"has_velocity": False, "daily_velocity": 0, "period_days": 0, "period_views": 0}

    history_sorted = sorted(history, key=lambda s: s["date"])
    oldest = history_sorted[0]
    newest = history_sorted[-1]

    try:
        d1 = datetime.strptime(oldest["date"], "%Y-%m-%d")
        d2 = datetime.strptime(newest["date"], "%Y-%m-%d")
    except ValueError:
        return {"has_velocity": False, "daily_velocity": 0, "period_days": 0, "period_views": 0}

    period_days = max((d2 - d1).days, 1)
    period_views = newest["views"] - oldest["views"]
    daily_velocity = round(period_views / period_days, 1)

    # 7-day velocity if enough data
    seven_days_ago = (d2 - timedelta(days=7)).strftime("%Y-%m-%d")
    recent_snaps = [s for s in history_sorted if s["date"] >= seven_days_ago]
    if len(recent_snaps) >= 2:
        r_oldest = recent_snaps[0]
        r_newest = recent_snaps[-1]
        r_days = max((datetime.strptime(r_newest["date"], "%Y-%m-%d") -
                      datetime.strptime(r_oldest["date"], "%Y-%m-%d")).days, 1)
        recent_velocity = round((r_newest["views"] - r_oldest["views"]) / r_days, 1)
    else:
        recent_velocity = daily_velocity

    return {
        "has_velocity": True,
        "daily_velocity": daily_velocity,
        "recent_velocity": recent_velocity,
        "period_days": period_days,
        "period_views": period_views,
        "trending_up": recent_velocity > daily_velocity * 1.2,
    }


def compute_longevity_from_snapshots(video: dict, snapshots: dict) -> float:
    """Compute longevity score using snapshot data.

    Longevity = recent_velocity / expected_velocity_for_age
    > 1.0 = evergreen (still getting more views than expected)
    < 1.0 = front-loaded (peaked and declining)
    """
    days_old = video.get("_days_old", 0)
    if days_old < 14:
        return 0  # Too new

    velocity = compute_velocity(video.get("video_id", ""), snapshots)
    if not velocity["has_velocity"]:
        return 0

    total_views = video.get("view_count", 0)
    if total_views == 0:
        return 0

    # Expected velocity: if views decayed normally, at this age
    # we'd expect about (total_views * 30) / (days_old * (days_old + 29)) views/day
    expected = (total_views * 30) / (days_old * (days_old + 29))
    if expected <= 0:
        return 0

    actual = velocity["daily_velocity"]
    return round(actual / expected, 1)


def get_market_summary(prices: dict, video_data: list) -> dict:
    """Generate market-segmented performance summary."""
    bull_videos = [v for v in video_data if v.get("_market_phase") == "bull"]
    bear_videos = [v for v in video_data if v.get("_market_phase") == "bear"]
    sideways_videos = [v for v in video_data if v.get("_market_phase") == "sideways"]

    def phase_stats(vids):
        if not vids:
            return {"count": 0, "avg_views": 0, "avg_engagement": 0}
        return {
            "count": len(vids),
            "avg_views": sum(v["view_count"] for v in vids) // len(vids),
            "avg_engagement": round(sum(v["engagement_rate"] for v in vids) / len(vids), 2),
        }

    return {
        "bull": phase_stats(bull_videos),
        "bear": phase_stats(bear_videos),
        "sideways": phase_stats(sideways_videos),
    }
