import os
import json
from flask import Flask, render_template, request, session, jsonify, redirect, url_for, send_file
from dotenv import load_dotenv
from youtube import fetch_playlist_videos, parse_playlist_id, fetch_channel_videos
from brainstorm import chat_with_claude, generate_video_plan, analyze_competitor_video, analyze_own_video, generate_trend_report, suggest_channels, remix_video
from market import (fetch_btc_prices, tag_video_market_phase, compute_longevity_score,
                    record_snapshot, compute_velocity, compute_longevity_from_snapshots,
                    get_current_market_info, get_market_summary)
from trends import analyze_trends
from analytics import get_oauth_flow, save_credentials, load_credentials, is_authenticated, fetch_channel_analytics

load_dotenv()

# Allow OAuth over HTTP for local development
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "yt-planner-secret-key")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
CACHE_FILE = os.path.join(DATA_DIR, "videos.json")
ANALYTICS_FILE = os.path.join(DATA_DIR, "analytics.json")
COMPETITORS_FILE = os.path.join(DATA_DIR, "competitors.json")
CATEGORIES_FILE = os.path.join(DATA_DIR, "video_categories.json")
TITLE_HISTORY_FILE = os.path.join(DATA_DIR, "title_history.json")

# In-memory store
video_cache = []
chat_history = []
playlist_info = {}
analytics_cache = {}
competitors_cache = []  # list of {"name", "url", "category", "channel_id", "videos": [...]}
title_history = []


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


# Background sync: debounced push after any save
import threading
_sync_timer = None
_sync_lock = threading.Lock()

def _schedule_sync_push():
    """Schedule a sync push 3 seconds from now (debounced)."""
    global _sync_timer
    with _sync_lock:
        if _sync_timer:
            _sync_timer.cancel()
        _sync_timer = threading.Timer(3.0, _do_sync_push)
        _sync_timer.daemon = True
        _sync_timer.start()

def _do_sync_push():
    """Run the actual sync push in a background thread."""
    try:
        from sync import push_to_gist, is_sync_configured
        if is_sync_configured():
            push_to_gist()
    except Exception as e:
        print(f"[Sync] Background push failed: {e}")


def save_settings(playlist_id="", google_key="", anthropic_key="", playlist_url="",
                   oauth_client_id="", oauth_client_secret="",
                   sync_gist_id="", sync_github_token=""):
    _ensure_data_dir()
    # Preserve existing fields not being explicitly set
    existing = load_settings()
    settings = {
        "playlist_id": playlist_id or existing.get("playlist_id", ""),
        "playlist_url": playlist_url or existing.get("playlist_url", ""),
        "google_key": google_key or existing.get("google_key", ""),
        "anthropic_key": anthropic_key or existing.get("anthropic_key", ""),
        "oauth_client_id": oauth_client_id or existing.get("oauth_client_id", ""),
        "oauth_client_secret": oauth_client_secret or existing.get("oauth_client_secret", ""),
        "sync_gist_id": sync_gist_id or existing.get("sync_gist_id", ""),
        "sync_github_token": sync_github_token or existing.get("sync_github_token", ""),
    }
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)
    _schedule_sync_push()


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}


def save_video_cache(videos: list):
    _ensure_data_dir()
    with open(CACHE_FILE, "w") as f:
        json.dump(videos, f)
    _schedule_sync_push()


def load_video_cache() -> list:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return []


def save_analytics(data: dict):
    _ensure_data_dir()
    with open(ANALYTICS_FILE, "w") as f:
        json.dump(data, f)
    _schedule_sync_push()


def load_analytics() -> dict:
    if os.path.exists(ANALYTICS_FILE):
        with open(ANALYTICS_FILE) as f:
            return json.load(f)
    return {}


def save_competitors(data: list):
    _ensure_data_dir()
    with open(COMPETITORS_FILE, "w") as f:
        json.dump(data, f)
    _schedule_sync_push()


def load_competitors() -> list:
    if os.path.exists(COMPETITORS_FILE):
        with open(COMPETITORS_FILE) as f:
            return json.load(f)
    return []


def save_categories(data: dict):
    _ensure_data_dir()
    with open(CATEGORIES_FILE, "w") as f:
        json.dump(data, f)
    _schedule_sync_push()


def load_categories() -> dict:
    if os.path.exists(CATEGORIES_FILE):
        with open(CATEGORIES_FILE) as f:
            return json.load(f)
    return {}


def save_title_history(data: list):
    _ensure_data_dir()
    with open(TITLE_HISTORY_FILE, "w") as f:
        json.dump(data, f)
    _schedule_sync_push()


def load_title_history() -> list:
    if os.path.exists(TITLE_HISTORY_FILE):
        with open(TITLE_HISTORY_FILE) as f:
            return json.load(f)
    return []


# Sync: pull latest data from Gist before loading
from sync import pull_from_gist, push_to_gist, is_sync_configured
try:
    pull_from_gist()
except Exception as _sync_err:
    print(f"[Sync] Pull on startup failed: {_sync_err}")

# Load persisted data on startup
_saved = load_settings()
playlist_info = {
    "playlist_id": _saved.get("playlist_id", ""),
    "google_key": _saved.get("google_key", ""),
}
video_cache = load_video_cache()
analytics_cache = load_analytics()
competitors_cache = load_competitors()
video_categories = load_categories()
title_history = load_title_history()

# Auto-refresh competitors on startup
_google_key = _saved.get("google_key", "")
if _google_key and competitors_cache:
    for _comp in competitors_cache:
        try:
            _result = fetch_channel_videos(_comp["url"], _google_key, max_videos=30)
            _comp["videos"] = _result["videos"]
            _comp["name"] = _result["channel_title"]
        except Exception:
            pass
    save_competitors(competitors_cache)

# Record view count snapshots for longevity tracking
if video_cache or competitors_cache:
    _snapshots = record_snapshot(video_cache, competitors_cache)
else:
    _snapshots = {}

# Fetch BTC price data (cached, non-blocking if fails)
_btc_prices = fetch_btc_prices()
if _btc_prices:
    _latest = sorted(_btc_prices.keys())[-1]
    print(f"[Market] BTC data loaded: {len(_btc_prices)} days, latest {_latest} = ${_btc_prices[_latest]:,.0f}")
else:
    print("[Market] WARNING: Could not fetch BTC price data. Market features will show $0.")


@app.route("/")
def index():
    if not video_cache:
        return redirect(url_for("settings_page"))

    from datetime import datetime, timedelta, timezone

    # Build a unified outlier feed from all inspiration channels
    outliers = []
    for comp in competitors_cache:
        vids = comp.get("videos", [])
        if not vids:
            continue
        avg = sum(v["view_count"] for v in vids) // len(vids) if vids else 1
        for v in vids:
            ratio = v["view_count"] / avg if avg > 0 else 0
            if ratio >= 1.2:
                vc = dict(v)
                vc["_channel"] = comp["name"]
                vc["_channel_id"] = comp["channel_id"]
                vc["_channel_category"] = comp.get("category", "")
                vc["_ratio"] = round(ratio, 1)
                try:
                    vc["_date"] = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
                except (ValueError, KeyError):
                    vc["_date"] = datetime.min.replace(tzinfo=timezone.utc)
                outliers.append(vc)

    outliers.sort(key=lambda v: v["_date"], reverse=True)
    outliers = outliers[:20]

    # Recent own videos with market-adjusted scores
    cutoff_1y = datetime.now(timezone.utc) - timedelta(days=365)
    year_videos = []
    for v in video_cache:
        try:
            dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
            if dt >= cutoff_1y:
                year_videos.append(v)
        except (ValueError, KeyError):
            pass

    year_avg_views = sum(v["view_count"] for v in year_videos) // len(year_videos) if year_videos else 1

    # Market-adjusted baselines
    bear_year = [v for v in year_videos if tag_video_market_phase(dict(v), _btc_prices).get("_market_phase") == "bear"]
    bull_year = [v for v in year_videos if tag_video_market_phase(dict(v), _btc_prices).get("_market_phase") == "bull"]
    bear_avg = sum(v["view_count"] for v in bear_year) // len(bear_year) if bear_year else year_avg_views
    bull_avg = sum(v["view_count"] for v in bull_year) // len(bull_year) if bull_year else year_avg_views

    market_info = get_current_market_info(_btc_prices)

    recent_videos = []
    for v in video_cache:
        try:
            dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            continue
        vc = dict(v)
        vc["_date"] = dt
        vc["_score"] = round(v["view_count"] / year_avg_views, 1) if year_avg_views else 0
        tag_video_market_phase(vc, _btc_prices)
        phase = vc.get("_market_phase", "unknown")
        phase_avg = bear_avg if phase == "bear" else (bull_avg if phase == "bull" else year_avg_views)
        vc["_market_score"] = round(v["view_count"] / phase_avg, 1) if phase_avg else 0
        vc["_category"] = video_categories.get(v["video_id"], "")
        recent_videos.append(vc)

    recent_videos.sort(key=lambda v: v["_date"], reverse=True)
    recent_videos = recent_videos[:20]

    # Check for trend seed from Trends → Plan This Video handoff
    trend_seed = session.pop("trend_seed", None)

    return render_template(
        "workspace.html",
        competitors=competitors_cache,
        outliers=outliers,
        recent_videos=recent_videos,
        market_info=market_info,
        chat_history=chat_history,
        trend_seed=trend_seed,
    )


@app.route("/settings")
def settings_page():
    settings = load_settings()
    return render_template(
        "settings.html",
        google_key=settings.get("google_key", "") or os.environ.get("GOOGLE_API_KEY", ""),
        anthropic_key=settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", ""),
        playlist_url=settings.get("playlist_url", ""),
        oauth_client_id=settings.get("oauth_client_id", ""),
        oauth_client_secret=settings.get("oauth_client_secret", ""),
        oauth_connected=is_authenticated(),
        has_analytics=bool(analytics_cache),
        has_videos=len(video_cache) > 0,
        error=request.args.get("error"),
        success=request.args.get("success"),
        videos=video_cache,
        video_categories=video_categories,
        sync_gist_id=settings.get("sync_gist_id", ""),
        sync_github_token=settings.get("sync_github_token", ""),
        sync_configured=is_sync_configured(),
    )


@app.route("/fetch", methods=["POST"])
def fetch():
    global video_cache, chat_history

    playlist_url = request.form.get("playlist_url", "").strip()
    google_key = request.form.get("google_key", "").strip()
    anthropic_key = request.form.get("anthropic_key", "").strip()
    oauth_client_id = request.form.get("oauth_client_id", "").strip()
    oauth_client_secret = request.form.get("oauth_client_secret", "").strip()
    sync_gist_id = request.form.get("sync_gist_id", "").strip()
    sync_github_token = request.form.get("sync_github_token", "").strip()

    if not playlist_url or not google_key:
        return render_template(
            "settings.html",
            error="Playlist URL and Google API key are required.",
            google_key=google_key,
            anthropic_key=anthropic_key,
            playlist_url=playlist_url,
            has_videos=len(video_cache) > 0,
        )

    try:
        playlist_id = parse_playlist_id(playlist_url)
        video_cache = fetch_playlist_videos(playlist_id, google_key)
        playlist_info["playlist_id"] = playlist_id
        playlist_info["google_key"] = google_key
        chat_history = []

        # Persist everything
        save_settings(playlist_id, google_key, anthropic_key, playlist_url,
                      oauth_client_id, oauth_client_secret,
                      sync_gist_id, sync_github_token)
        save_video_cache(video_cache)
    except Exception as e:
        return render_template(
            "settings.html",
            error=f"Failed to fetch playlist: {e}",
            google_key=google_key,
            anthropic_key=anthropic_key,
            playlist_url=playlist_url,
            has_videos=len(video_cache) > 0,
        )

    return redirect(url_for("dashboard"))


@app.route("/save-settings", methods=["POST"])
def save_settings_only():
    """Save settings without re-fetching the playlist."""
    playlist_url = request.form.get("playlist_url", "").strip()
    google_key = request.form.get("google_key", "").strip()
    anthropic_key = request.form.get("anthropic_key", "").strip()
    oauth_client_id = request.form.get("oauth_client_id", "").strip()
    oauth_client_secret = request.form.get("oauth_client_secret", "").strip()
    sync_gist_id = request.form.get("sync_gist_id", "").strip()
    sync_github_token = request.form.get("sync_github_token", "").strip()

    existing = load_settings()
    playlist_id = existing.get("playlist_id", "")
    if playlist_url:
        try:
            playlist_id = parse_playlist_id(playlist_url)
        except Exception:
            playlist_id = existing.get("playlist_id", "")

    save_settings(playlist_id, google_key, anthropic_key, playlist_url,
                  oauth_client_id, oauth_client_secret,
                  sync_gist_id, sync_github_token)

    return redirect(url_for("settings_page", success="Settings saved."))


@app.route("/dashboard")
def dashboard():
    if not video_cache:
        return redirect(url_for("index"))

    sort_by = request.args.get("sort", "view_count")
    order = request.args.get("order", "desc")
    valid_sorts = ["view_count", "like_count", "comment_count", "engagement_rate", "published_at", "title"]

    if sort_by not in valid_sorts:
        sort_by = "view_count"

    reverse = order == "desc"
    sorted_videos = sorted(video_cache, key=lambda v: v.get(sort_by, 0), reverse=reverse)

    total_views = sum(v["view_count"] for v in video_cache)
    avg_views = total_views // len(video_cache) if video_cache else 0
    avg_engagement = round(sum(v["engagement_rate"] for v in video_cache) / len(video_cache), 2) if video_cache else 0

    return render_template(
        "dashboard.html",
        videos=sorted_videos,
        total_videos=len(video_cache),
        total_views=total_views,
        avg_views=avg_views,
        avg_engagement=avg_engagement,
        sort_by=sort_by,
        order=order,
        chat_history=chat_history,
    )


@app.route("/api/chat", methods=["POST"])
def api_chat():
    global chat_history

    data = request.get_json()
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Message is required"}), 400

    plan_state = data.get("plan_state")

    settings = load_settings()
    api_key = settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured. Go back to the home page to set it."}), 400

    try:
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        reply = chat_with_claude(user_message, video_cache, chat_history, api_key, analytics_cache, competitors_cache, market_data=market_data, plan_state=plan_state)

        # Check if reply contains a plan change JSON block
        plan_change = None
        if "```plan_change" in reply:
            import re
            match = re.search(r"```plan_change\s*\n([\s\S]*?)```", reply)
            if match:
                try:
                    plan_change = json.loads(match.group(1))
                    # Remove the JSON block from the displayed reply
                    reply = reply[:match.start()].rstrip() + reply[match.end():].lstrip()
                except json.JSONDecodeError:
                    pass

        chat_history.append({"role": "user", "content": user_message})
        chat_history.append({"role": "assistant", "content": reply})

        result = {"reply": reply}
        if plan_change:
            result["plan_change"] = plan_change
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trends")
def trends_page():
    if not video_cache:
        return redirect(url_for("index"))

    from datetime import datetime, timedelta, timezone
    cutoff_1y = datetime.now(timezone.utc) - timedelta(days=365)

    # Past-year videos for baseline
    year_videos = []
    for v in video_cache:
        try:
            dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
            if dt >= cutoff_1y:
                year_videos.append(v)
        except (ValueError, KeyError):
            pass

    year_avg_views = sum(v["view_count"] for v in year_videos) // len(year_videos) if year_videos else 1
    year_avg_likes = sum(v.get("like_count", 0) for v in year_videos) // len(year_videos) if year_videos else 0
    year_avg_engagement = round(sum(v["engagement_rate"] for v in year_videos) / len(year_videos), 2) if year_videos else 0
    year_total_views = sum(v["view_count"] for v in year_videos)

    # Market data
    market_info = get_current_market_info(_btc_prices)

    # Market-adjusted baselines
    bear_year = [v for v in year_videos if tag_video_market_phase(dict(v), _btc_prices).get("_market_phase") == "bear"]
    bull_year = [v for v in year_videos if tag_video_market_phase(dict(v), _btc_prices).get("_market_phase") == "bull"]
    bear_avg = sum(v["view_count"] for v in bear_year) // len(bear_year) if bear_year else year_avg_views
    bull_avg = sum(v["view_count"] for v in bull_year) // len(bull_year) if bull_year else year_avg_views

    # Sort own videos by publish date (most recent first), add scores
    snapshots = _snapshots
    dated_videos = []
    for v in video_cache:
        try:
            dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
            vc = dict(v)
            vc["_date"] = dt
            vc["_score"] = round(v["view_count"] / year_avg_views, 1) if year_avg_views else 0

            # Market phase tagging
            tag_video_market_phase(vc, _btc_prices)
            phase = vc.get("_market_phase", "unknown")
            phase_avg = bear_avg if phase == "bear" else (bull_avg if phase == "bull" else year_avg_views)
            vc["_market_score"] = round(v["view_count"] / phase_avg, 1) if phase_avg else 0

            # Longevity
            compute_longevity_score(vc)
            vel = compute_velocity(v["video_id"], snapshots)
            vc["_velocity"] = vel
            if vel["has_velocity"]:
                vc["_longevity_score"] = compute_longevity_from_snapshots(vc, snapshots)

            dated_videos.append(vc)
        except (ValueError, KeyError):
            dated_videos.append(dict(v, _date=None, _score=0, _market_phase="unknown",
                                     _btc_price_at_publish=0, _market_score=0,
                                     _days_old=0, _views_per_day=0, _longevity_score=0,
                                     _velocity={"has_velocity": False}))

    dated_videos.sort(key=lambda v: v["_date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # Market-segmented performance
    market_summary = get_market_summary(_btc_prices, dated_videos)

    # Competitor videos: sort by date, keep top recent performers (8 per channel)
    scored_competitors = []
    for comp in competitors_cache:
        vids = comp.get("videos", [])
        if not vids:
            scored_competitors.append(comp)
            continue
        comp_avg = sum(v["view_count"] for v in vids) // len(vids) if vids else 1
        dated_comp = []
        for v in vids:
            try:
                dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
                vc = dict(v)
                vc["_date"] = dt
                vc["_score"] = round(v["view_count"] / comp_avg, 1) if comp_avg else 0
                tag_video_market_phase(vc, _btc_prices)
                compute_longevity_score(vc)
                dated_comp.append(vc)
            except (ValueError, KeyError):
                dated_comp.append(dict(v, _date=None, _score=0, _market_phase="unknown",
                                       _days_old=0, _views_per_day=0))
        dated_comp.sort(key=lambda v: v["_date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        recent = dated_comp[:20]
        recent.sort(key=lambda v: v["view_count"], reverse=True)
        top_recent = recent[:8]
        top_recent.sort(key=lambda v: v["_date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        scored_competitors.append({**comp, "scored_videos": top_recent, "avg_views": comp_avg})

    total_views = sum(v["view_count"] for v in video_cache)
    avg_engagement = round(sum(v["engagement_rate"] for v in video_cache) / len(video_cache), 2)
    trends_data = analyze_trends(dated_videos, competitors_cache)

    return render_template("trends.html", trends=trends_data,
                           total_videos=len(video_cache),
                           competitor_count=len(competitors_cache),
                           videos=dated_videos,
                           total_views=total_views,
                           avg_engagement=avg_engagement,
                           year_avg_views=year_avg_views,
                           year_avg_likes=year_avg_likes,
                           year_avg_engagement=year_avg_engagement,
                           year_total_views=year_total_views,
                           year_video_count=len(year_videos),
                           bear_avg=bear_avg,
                           bull_avg=bull_avg,
                           market_info=market_info,
                           market_summary=market_summary,
                           competitors=scored_competitors)


@app.route("/planner")
def planner():
    # Attach categories to videos
    tagged_videos = []
    for v in video_cache:
        vc = dict(v)
        vc["_category"] = video_categories.get(v["video_id"], "")
        tagged_videos.append(vc)

    # Compute format performance stats
    format_stats = {}
    uncategorized_count = 0
    for v in tagged_videos:
        cat = v["_category"]
        if not cat:
            uncategorized_count += 1
            continue
        if cat not in format_stats:
            format_stats[cat] = {"count": 0, "views": 0, "engagement": 0}
        format_stats[cat]["count"] += 1
        format_stats[cat]["views"] += v["view_count"]
        format_stats[cat]["engagement"] += v.get("engagement_rate", 0)

    for cat, stats in format_stats.items():
        stats["avg_views"] = stats["views"] // stats["count"] if stats["count"] else 0
        stats["avg_engagement"] = round(stats["engagement"] / stats["count"], 2) if stats["count"] else 0

    return render_template("planner.html",
                           videos=tagged_videos,
                           format_stats=format_stats,
                           uncategorized_count=uncategorized_count)


@app.route("/api/generate-plan", methods=["POST"])
def api_generate_plan():
    data = request.get_json()
    topic = data.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    video_type = data.get("video_type", "tutorial")
    links = data.get("links", [])
    competitors = data.get("competitors", [])
    notes = data.get("notes", "")

    settings = load_settings()
    api_key = settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured. Set it in Settings."}), 400

    trend_context = data.get("trend_context")

    try:
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }

        # Gather real-time trend intel
        trend_intel_context = ""
        try:
            from trend_intel import gather_trend_intel, intel_to_prompt_context
            intel = gather_trend_intel()
            trend_intel_context = intel_to_prompt_context(intel)
        except Exception as e:
            print(f"[Plan] Trend intel fetch failed: {e}")

        plan = generate_video_plan(
            video_type=video_type,
            topic=topic,
            links=links,
            competitors=competitors,
            notes=notes,
            video_data=video_cache,
            analytics_data=analytics_cache,
            competitors_data=competitors_cache,
            api_key=api_key,
            market_data=market_data,
            trend_context=trend_context,
            trend_intel_context=trend_intel_context,
            title_history=title_history,
        )
        plan["_scoring_ctx"] = _build_scoring_context()
        return jsonify(plan)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _build_scoring_context() -> dict:
    """Build title scoring context from real channel + competitor data."""
    from trends import analyze_trends
    from datetime import datetime, timedelta
    try:
        trends = analyze_trends(video_cache, competitors_cache)
    except Exception:
        trends = {}

    now = datetime.now()
    stop_words = {"the", "a", "an", "i", "my", "is", "it", "in", "to", "for", "of", "and",
                  "on", "with", "this", "that", "you", "your", "but", "not", "are", "was",
                  "be", "or", "at", "by", "we", "from", "so", "if", "do", "no", "just",
                  "its", "me", "has", "have", "had", "can", "will", "one", "all", "get",
                  "new", "been", "than", "up", "out", "about", "how", "what", "when", "why"}

    # Extract learned keywords with recency decay + performance weighting
    learned_keywords = {}
    for entry in title_history:
        # Recency decay: half-life of 90 days
        try:
            saved_date = datetime.fromisoformat(entry.get("saved_at", "2020-01-01"))
        except (ValueError, TypeError):
            saved_date = now - timedelta(days=365)
        days_ago = (now - saved_date).days
        recency_weight = 2 ** (-days_ago / 90)  # 1.0 today, 0.5 at 90 days, 0.25 at 180

        # Performance weight: if used and has real data, boost/penalize
        perf_weight = 1.0
        if entry.get("used") and entry.get("perf_ratio"):
            perf_weight = min(3.0, max(0.3, entry["perf_ratio"]))

        combined_weight = recency_weight * perf_weight

        words = entry["title"].lower().split()
        for w in words:
            w = w.strip("()[].,!?:;\"'")
            if len(w) > 2 and w not in stop_words:
                learned_keywords[w] = learned_keywords.get(w, 0) + combined_weight

    # Only keep words with meaningful weight (roughly 2+ recent titles)
    learned_keywords = {
        k: round(v, 2)
        for k, v in learned_keywords.items()
        if v >= 1.0
    }

    return {
        "hot_keywords": trends.get("hot_keywords", {}),
        "title_patterns": trends.get("title_patterns", {}),
        "overall_avg": trends.get("overall_avg_views", 1),
        "saved_title_count": len(title_history),
        "learned_keywords": learned_keywords,
    }


@app.route("/api/regenerate-titles", methods=["POST"])
def api_regenerate_titles():
    """Generate new title suggestions for an existing plan."""
    data = request.get_json()
    topic = data.get("topic", "").strip()
    video_type = data.get("video_type", "tutorial")
    notes = data.get("notes", "")
    trend_context = data.get("trend_context")

    settings = load_settings()
    api_key = settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured."}), 400

    try:
        from brainstorm import regenerate_titles
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        titles = regenerate_titles(
            video_type=video_type,
            topic=topic,
            notes=notes,
            video_data=video_cache,
            api_key=api_key,
            market_data=market_data,
            trend_context=trend_context,
            title_history=title_history,
        )
        return jsonify({"titles": titles, "_scoring_ctx": _build_scoring_context()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/title-history", methods=["GET"])
def api_get_title_history():
    return jsonify({"titles": title_history})


@app.route("/api/title-history/save", methods=["POST"])
def api_save_title():
    global title_history
    data = request.get_json()
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400

    from datetime import datetime
    entry = {
        "title": title,
        "topic": data.get("topic", ""),
        "format": data.get("format", ""),
        "score": data.get("score", 0),
        "original": data.get("original", ""),
        "saved_at": datetime.now().isoformat()[:10],
        "used": False,
    }
    title_history.append(entry)
    save_title_history(title_history)
    return jsonify({"ok": True, "count": len(title_history)})


@app.route("/api/title-history/delete", methods=["POST"])
def api_delete_title():
    global title_history
    data = request.get_json()
    idx = data.get("index")
    if idx is None or idx < 0 or idx >= len(title_history):
        return jsonify({"error": "Invalid index"}), 400
    title_history.pop(idx)
    save_title_history(title_history)
    return jsonify({"ok": True})


@app.route("/api/title-history/mark-used", methods=["POST"])
def api_mark_title_used():
    global title_history
    data = request.get_json()
    idx = data.get("index")
    if idx is None or idx < 0 or idx >= len(title_history):
        return jsonify({"error": "Invalid index"}), 400

    from datetime import datetime

    title_history[idx]["used"] = True
    title_history[idx]["used_at"] = datetime.now().isoformat()[:10]
    title_history[idx]["video_url"] = data.get("video_url", "")

    # Auto-match to actual video performance from playlist
    saved_title = title_history[idx]["title"].lower().strip()
    best_match = None
    best_ratio = 0
    for v in video_cache:
        vt = v["title"].lower().strip()
        # Check for close match (exact or high overlap)
        if vt == saved_title:
            best_match = v
            break
        # Fuzzy: count shared words
        saved_words = set(saved_title.split())
        vid_words = set(vt.split())
        if not saved_words:
            continue
        overlap = len(saved_words & vid_words) / max(len(saved_words), len(vid_words))
        if overlap > best_ratio and overlap >= 0.6:
            best_ratio = overlap
            best_match = v

    if best_match:
        avg_views = sum(v["view_count"] for v in video_cache) // len(video_cache) if video_cache else 1
        perf_ratio = round(best_match["view_count"] / avg_views, 2) if avg_views else 0
        title_history[idx]["video_id"] = best_match["video_id"]
        title_history[idx]["actual_views"] = best_match["view_count"]
        title_history[idx]["perf_ratio"] = perf_ratio
        title_history[idx]["engagement_rate"] = best_match.get("engagement_rate", 0)
        # Tag market phase
        tagged = tag_video_market_phase(dict(best_match), _btc_prices)
        title_history[idx]["market_phase"] = tagged.get("_market_phase", "unknown")

    save_title_history(title_history)
    return jsonify({"ok": True, "matched": best_match is not None})


@app.route("/api/plan-context", methods=["POST"])
def api_plan_context():
    """Store trend context in session for Trends → Workspace handoff."""
    data = request.get_json()
    session["trend_seed"] = {
        "title": data.get("title", ""),
        "source": data.get("source", ""),
        "category": data.get("category", ""),
        "thumb": data.get("thumb", ""),
        "analysis_summary": data.get("analysis_summary", ""),
        "focus_topic": data.get("focus_topic", ""),
        "remix_ideas": data.get("remix_ideas", []),
        "style_takeaways": data.get("style_takeaways", []),
    }
    return jsonify({"ok": True})


@app.route("/competitors")
def competitors_page():
    # Backfill avatars for channels that don't have one yet
    settings = load_settings()
    google_key = settings.get("google_key", "") or os.environ.get("GOOGLE_API_KEY", "")
    if google_key:
        needs_save = False
        for comp in competitors_cache:
            if not comp.get("avatar_url"):
                try:
                    from youtube import resolve_channel_id
                    _, _, avatar = resolve_channel_id(comp["url"], google_key)
                    comp["avatar_url"] = avatar
                    needs_save = True
                except Exception:
                    pass
        if needs_save:
            save_competitors(competitors_cache)
    return render_template("competitors.html", competitors=competitors_cache)


@app.route("/api/competitors/add", methods=["POST"])
def api_add_competitor():
    global competitors_cache
    data = request.get_json()
    channel_url = data.get("url", "").strip()
    category = data.get("category", "").strip()
    custom_label = data.get("custom_label", "").strip()

    if not channel_url:
        return jsonify({"error": "Channel URL is required"}), 400
    if not category:
        return jsonify({"error": "Category is required"}), 400

    settings = load_settings()
    google_key = settings.get("google_key", "")
    if not google_key:
        return jsonify({"error": "Google API key not configured. Set it in Settings."}), 400

    try:
        result = fetch_channel_videos(channel_url, google_key, max_videos=30)
        label = custom_label if category == "custom" else category
        competitor = {
            "name": result["channel_title"],
            "url": channel_url,
            "channel_id": result["channel_id"],
            "category": label,
            "avatar_url": result.get("avatar_url", ""),
            "videos": result["videos"],
        }

        # Don't add duplicates
        for existing in competitors_cache:
            if existing["channel_id"] == competitor["channel_id"]:
                return jsonify({"error": f"{competitor['name']} is already added."}), 400

        competitors_cache.append(competitor)
        save_competitors(competitors_cache)
        return jsonify({"ok": True, "competitor": {
            "name": competitor["name"],
            "category": competitor["category"],
            "video_count": len(competitor["videos"]),
        }})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/competitors/remove", methods=["POST"])
def api_remove_competitor():
    global competitors_cache
    data = request.get_json()
    channel_id = data.get("channel_id", "")
    competitors_cache = [c for c in competitors_cache if c["channel_id"] != channel_id]
    save_competitors(competitors_cache)
    return jsonify({"ok": True})


@app.route("/api/competitors/update-category", methods=["POST"])
def api_update_competitor_category():
    global competitors_cache
    data = request.get_json()
    channel_id = data.get("channel_id", "")
    category = data.get("category", "").strip()
    if not channel_id or not category:
        return jsonify({"error": "channel_id and category required"}), 400
    for comp in competitors_cache:
        if comp["channel_id"] == channel_id:
            comp["category"] = category
            break
    save_competitors(competitors_cache)
    return jsonify({"ok": True})


@app.route("/api/competitors/refresh", methods=["POST"])
def api_refresh_competitors():
    global competitors_cache
    settings = load_settings()
    google_key = settings.get("google_key", "")
    if not google_key:
        return jsonify({"error": "Google API key not configured."}), 400

    errors = []
    for comp in competitors_cache:
        try:
            result = fetch_channel_videos(comp["url"], google_key, max_videos=30)
            comp["videos"] = result["videos"]
            comp["name"] = result["channel_title"]
            comp["avatar_url"] = result.get("avatar_url", "")
        except Exception as e:
            errors.append(f"{comp['name']}: {e}")

    save_competitors(competitors_cache)
    if errors:
        return jsonify({"ok": True, "warnings": errors})
    return jsonify({"ok": True})


@app.route("/api/suggest-channels", methods=["POST"])
def api_suggest_channels():
    settings = load_settings()
    api_key = settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured."}), 400

    if not video_cache:
        return jsonify({"error": "No videos loaded. Fetch your playlist first."}), 400

    try:
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        result = suggest_channels(
            video_data=video_cache,
            competitors_data=competitors_cache,
            api_key=api_key,
            market_data=market_data,
        )

        # Enrich suggestions with avatars from YouTube API
        google_key = settings.get("google_key", "") or os.environ.get("GOOGLE_API_KEY", "")
        if google_key:
            from youtube import resolve_channel_id
            for group in ["crypto_channels", "tech_channels"]:
                for ch in result.get(group, []):
                    try:
                        _, _, avatar = resolve_channel_id(ch["handle"], google_key)
                        ch["avatar_url"] = avatar
                    except Exception:
                        ch["avatar_url"] = ""

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/oauth/connect")
def oauth_connect():
    """Start the OAuth flow for YouTube Analytics."""
    settings = load_settings()
    client_id = settings.get("oauth_client_id", "")
    client_secret = settings.get("oauth_client_secret", "")
    if not client_id or not client_secret:
        return redirect(url_for("settings_page", error="Set OAuth Client ID and Secret in Settings first."))

    redirect_uri = url_for("oauth_callback", _external=True)
    flow = get_oauth_flow(client_id, client_secret, redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["oauth_state"] = state
    # Save the code verifier for PKCE
    session["code_verifier"] = flow.code_verifier
    return redirect(auth_url)


@app.route("/oauth/callback")
def oauth_callback():
    """Handle the OAuth callback from Google."""
    settings = load_settings()
    client_id = settings.get("oauth_client_id", "")
    client_secret = settings.get("oauth_client_secret", "")

    redirect_uri = url_for("oauth_callback", _external=True)
    flow = get_oauth_flow(client_id, client_secret, redirect_uri)
    # Restore the code verifier from the session
    flow.code_verifier = session.get("code_verifier")

    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    save_credentials(creds)

    return redirect(url_for("settings_page"))


@app.route("/api/fetch-analytics", methods=["POST"])
def api_fetch_analytics():
    global analytics_cache
    try:
        analytics_cache = fetch_channel_analytics()
        save_analytics(analytics_cache)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-trend-report", methods=["POST"])
def api_generate_trend_report():
    settings = load_settings()
    api_key = settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured."}), 400
    if not video_cache:
        return jsonify({"error": "No video data available."}), 400

    trends_data = analyze_trends(video_cache, competitors_cache)
    try:
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        report = generate_trend_report(
            video_data=video_cache,
            competitors_data=competitors_cache,
            trends_data=trends_data,
            api_key=api_key,
            analytics_data=analytics_cache,
            market_data=market_data,
        )
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze-own-video", methods=["POST"])
def api_analyze_own_video():
    data = request.get_json()
    video_id = data.get("video_id", "")

    if not video_id:
        return jsonify({"error": "video_id is required"}), 400

    settings = load_settings()
    api_key = settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured."}), 400

    video = None
    for v in video_cache:
        if v["video_id"] == video_id:
            video = v
            break

    if not video:
        return jsonify({"error": "Video not found."}), 404

    try:
        # Enrich video with market phase data
        video = dict(video)
        tag_video_market_phase(video, _btc_prices)

        # Compute market-adjusted score
        from datetime import datetime, timedelta, timezone
        cutoff_1y = datetime.now(timezone.utc) - timedelta(days=365)
        year_videos = []
        for v in video_cache:
            try:
                dt = datetime.fromisoformat(v["published_at"].replace("Z", "+00:00"))
                if dt >= cutoff_1y:
                    year_videos.append(v)
            except (ValueError, KeyError):
                pass
        year_avg = sum(v["view_count"] for v in year_videos) // len(year_videos) if year_videos else 1
        bear_vids = [v for v in year_videos if tag_video_market_phase(dict(v), _btc_prices).get("_market_phase") == "bear"]
        bull_vids = [v for v in year_videos if tag_video_market_phase(dict(v), _btc_prices).get("_market_phase") == "bull"]
        bear_avg = sum(v["view_count"] for v in bear_vids) // len(bear_vids) if bear_vids else year_avg
        bull_avg = sum(v["view_count"] for v in bull_vids) // len(bull_vids) if bull_vids else year_avg
        phase = video.get("_market_phase", "unknown")
        phase_avg = bear_avg if phase == "bear" else (bull_avg if phase == "bull" else year_avg)
        video["_market_score"] = round(video["view_count"] / phase_avg, 1) if phase_avg else 0
        video["_phase_avg"] = phase_avg
        video["_bear_avg"] = bear_avg
        video["_bull_avg"] = bull_avg

        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        analysis = analyze_own_video(
            video=video,
            video_data=video_cache,
            api_key=api_key,
            analytics_data=analytics_cache,
            competitors_data=competitors_cache,
            market_data=market_data,
        )
        return jsonify(analysis)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze-competitor-video", methods=["POST"])
def api_analyze_competitor_video():
    data = request.get_json()
    channel_id = data.get("channel_id", "")
    video_id = data.get("video_id", "")

    if not channel_id or not video_id:
        return jsonify({"error": "channel_id and video_id are required"}), 400

    settings = load_settings()
    api_key = settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured."}), 400

    # Find the competitor and video
    competitor = None
    video = None
    for comp in competitors_cache:
        if comp["channel_id"] == channel_id:
            competitor = comp
            for v in comp["videos"]:
                if v["video_id"] == video_id:
                    video = v
                    break
            break

    if not competitor or not video:
        return jsonify({"error": "Competitor or video not found."}), 404

    try:
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        analysis = analyze_competitor_video(
            video=video,
            competitor=competitor,
            video_data=video_cache,
            api_key=api_key,
            analytics_data=analytics_cache,
            market_data=market_data,
        )
        return jsonify(analysis)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/remix-video", methods=["POST"])
def api_remix_video():
    data = request.get_json()
    video_title = data.get("video_title", "")
    video_source = data.get("video_source", "You")
    source_category = data.get("source_category", "Bitcoin/Crypto")
    analysis_summary = data.get("analysis_summary", "")
    focus_topic = data.get("focus_topic", "")

    if not video_title:
        return jsonify({"error": "video_title is required"}), 400

    settings = load_settings()
    api_key = settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured."}), 400

    try:
        from trend_intel import gather_trend_intel, intel_to_prompt_context
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        intel = gather_trend_intel()
        intel_context = intel_to_prompt_context(intel)
        result = remix_video(
            video_title=video_title,
            video_source=video_source,
            source_category=source_category,
            analysis_summary=analysis_summary,
            video_data=video_cache,
            api_key=api_key,
            focus_topic=focus_topic,
            market_data=market_data,
            trend_intel_context=intel_context,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/update", methods=["POST"])
def update_app():
    import subprocess
    app_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        # Detect the current branch
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=app_dir,
        )
        branch = branch_result.stdout.strip() or "claude/youtube-planning-app-B52L2"

        result = subprocess.run(
            ["git", "pull", "origin", branch],
            capture_output=True, text=True, timeout=30, cwd=app_dir,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip() or "Git pull failed"}), 500
        if "Already up to date" in output:
            return jsonify({"updated": False, "message": "Already up to date."})
        return jsonify({"updated": True, "message": output})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/video-category", methods=["POST"])
def api_video_category():
    global video_categories
    data = request.get_json()
    video_id = data.get("video_id", "")
    category = data.get("category", "")

    if not video_id:
        return jsonify({"error": "video_id is required"}), 400

    if category:
        video_categories[video_id] = category
    else:
        video_categories.pop(video_id, None)

    save_categories(video_categories)
    return jsonify({"ok": True})


@app.route("/api/video-category/bulk", methods=["POST"])
def api_video_category_bulk():
    global video_categories
    data = request.get_json()
    assignments = data.get("assignments", [])
    if not assignments:
        return jsonify({"error": "No assignments provided"}), 400

    for item in assignments:
        video_id = item.get("video_id", "")
        category = item.get("category", "")
        if not video_id:
            continue
        if category:
            video_categories[video_id] = category
        else:
            video_categories.pop(video_id, None)

    save_categories(video_categories)
    return jsonify({"ok": True, "count": len(assignments)})


@app.route("/api/clear-chat", methods=["POST"])
def clear_chat():
    global chat_history
    chat_history = []
    return jsonify({"ok": True})


@app.route("/api/sync/create-gist", methods=["POST"])
def api_create_sync_gist():
    """Create a new private Gist for syncing."""
    data = request.get_json()
    token = data.get("token", "")
    if not token:
        return jsonify({"error": "GitHub token is required"}), 400

    from sync import create_sync_gist
    gist_id = create_sync_gist(token)
    if not gist_id:
        return jsonify({"error": "Failed to create Gist. Check your token has 'gist' scope."}), 400

    # Save to settings
    settings = load_settings()
    settings["sync_gist_id"] = gist_id
    settings["sync_github_token"] = token
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

    # Immediately push current data
    from sync import push_to_gist
    push_to_gist()

    return jsonify({"ok": True, "gist_id": gist_id})


@app.route("/api/sync/connect", methods=["POST"])
def api_sync_connect():
    """Connect to an existing Gist for syncing."""
    data = request.get_json()
    token = data.get("token", "")
    gist_id = data.get("gist_id", "")
    if not token or not gist_id:
        return jsonify({"error": "Both token and gist_id are required"}), 400

    # Save sync config
    settings = load_settings()
    settings["sync_gist_id"] = gist_id
    settings["sync_github_token"] = token
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

    # Pull data from the existing Gist
    global video_cache, analytics_cache, competitors_cache, video_categories, title_history
    pull_from_gist()
    video_cache = load_video_cache()
    analytics_cache = load_analytics()
    competitors_cache = load_competitors()
    video_categories = load_categories()
    title_history = load_title_history()

    return jsonify({"ok": True})


@app.route("/api/sync/disconnect", methods=["POST"])
def api_sync_disconnect():
    """Disconnect sync by clearing Gist settings."""
    settings = load_settings()
    settings.pop("sync_gist_id", None)
    settings.pop("sync_github_token", None)
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)
    return jsonify({"ok": True})


@app.route("/api/sync/update-token", methods=["POST"])
def api_sync_update_token():
    """Update the GitHub token and/or Gist ID for an existing sync connection."""
    data = request.get_json()
    token = data.get("token", "").strip()
    gist_id = data.get("gist_id", "").strip()
    if not token and not gist_id:
        return jsonify({"error": "Enter a token or Gist ID to update"}), 400
    settings = load_settings()
    if token:
        settings["sync_github_token"] = token
    if gist_id:
        settings["sync_gist_id"] = gist_id
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)
    # Push immediately so the Gist has the updated credentials
    from sync import push_to_gist
    try:
        push_to_gist()
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/sync/push", methods=["POST"])
def api_sync_push():
    """Manually trigger a sync push."""
    if not is_sync_configured():
        return jsonify({"error": "Sync not configured"}), 400
    success = push_to_gist()
    if success:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Push failed — check your GitHub token and Gist ID"}), 500


@app.route("/api/sync/pull", methods=["POST"])
def api_sync_pull():
    """Manually trigger a sync pull."""
    if not is_sync_configured():
        return jsonify({"error": "Sync not configured"}), 400
    global video_cache, analytics_cache, competitors_cache, video_categories, title_history
    updated = pull_from_gist()
    if updated:
        video_cache = load_video_cache()
        analytics_cache = load_analytics()
        competitors_cache = load_competitors()
        video_categories = load_categories()
        title_history = load_title_history()
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/export-data")
def api_export_data():
    """Export all app data as a zip file for migration to another machine."""
    import zipfile
    import io
    from datetime import datetime as _dt

    buf = io.BytesIO()
    data_files = [
        SETTINGS_FILE, CACHE_FILE, ANALYTICS_FILE,
        COMPETITORS_FILE, CATEGORIES_FILE,
    ]
    # Also include market and snapshot data
    market_dir = os.path.join(DATA_DIR)
    for name in ["btc_prices.json", "snapshots.json", "trend_intel.json", "oauth_token.json", "nostr_history.json"]:
        path = os.path.join(market_dir, name)
        if os.path.exists(path):
            data_files.append(path)

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in data_files:
            if os.path.exists(fpath):
                zf.write(fpath, os.path.basename(fpath))

    buf.seek(0)
    date_str = _dt.now().strftime("%Y%m%d")
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"sessions-builder-data-{date_str}.zip")


@app.route("/api/import-data", methods=["POST"])
def api_import_data():
    """Import a data zip file, replacing current app data."""
    import zipfile
    import io

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename.endswith(".zip"):
        return jsonify({"error": "File must be a .zip archive"}), 400

    try:
        _ensure_data_dir()
        with zipfile.ZipFile(io.BytesIO(f.read()), "r") as zf:
            # Only extract known JSON files
            allowed = {
                "settings.json", "videos.json", "analytics.json",
                "competitors.json", "video_categories.json",
                "btc_prices.json", "snapshots.json", "trend_intel.json",
                "oauth_token.json",
                "nostr_history.json", "title_history.json",
            }
            for name in zf.namelist():
                if name in allowed:
                    zf.extract(name, DATA_DIR)

        # Reload in-memory caches
        global video_cache, analytics_cache, competitors_cache, title_history
        video_cache = load_video_cache()
        analytics_cache = load_analytics()
        competitors_cache = load_competitors()
        title_history = load_title_history()

        return jsonify({"ok": True, "message": "Data imported. Refresh the page."})
    except zipfile.BadZipFile:
        return jsonify({"error": "Invalid zip file"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/shutdown", methods=["POST"])
def shutdown_app():
    """Gracefully shut down the Flask server."""
    import signal
    pid = os.getpid()
    # Send response first, then schedule shutdown
    def _kill():
        import time
        time.sleep(0.5)
        os.kill(pid, signal.SIGTERM)
    t = threading.Thread(target=_kill, daemon=True)
    t.start()
    return jsonify({"ok": True, "message": "Server shutting down..."})


if __name__ == "__main__":
    # Disable reloader when launched from .app bundle (keeps process alive for Dock)
    use_reloader = os.environ.get("LAUNCHED_FROM_APP") != "1"
    app.run(debug=True, host="127.0.0.1", port=5000, use_reloader=use_reloader)
