import os
import json
from flask import Flask, render_template, request, session, jsonify, redirect, url_for, send_file
from dotenv import load_dotenv
from youtube import fetch_playlist_videos, parse_playlist_id, fetch_channel_videos
from brainstorm import chat_with_claude, generate_video_plan, suggest_channels, DEFAULT_CHANNEL_NICHE
from market import (fetch_btc_prices, tag_video_market_phase, record_snapshot,
                    get_current_market_info, get_market_summary)
from trends import analyze_trends
from analytics import get_oauth_flow, save_credentials, load_credentials, is_authenticated, fetch_channel_analytics, fetch_retention_curves
import llm
import backend_client

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
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")

# In-memory store
video_cache = []
chat_history = []
playlist_info = {}
analytics_cache = {}
competitors_cache = []  # list of {"name", "url", "category", "channel_id", "videos": [...]}
plans_cache = []  # list of saved plans with performance tracking
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
                   sync_gist_id="", sync_github_token="", sync_password="",
                   channel_niche=None, channel_name=None, ai_settings=None,
                   extra_settings=None):
    _ensure_data_dir()
    # Start from the existing settings so fields managed elsewhere
    # (affiliate_links, sponsor_template, default_yt_tags, ...) survive a save.
    settings = load_settings()
    settings.update({
        "playlist_id": playlist_id or settings.get("playlist_id", ""),
        "playlist_url": playlist_url or settings.get("playlist_url", ""),
        "google_key": google_key or settings.get("google_key", ""),
        "anthropic_key": anthropic_key or settings.get("anthropic_key", ""),
        "oauth_client_id": oauth_client_id or settings.get("oauth_client_id", ""),
        "oauth_client_secret": oauth_client_secret or settings.get("oauth_client_secret", ""),
        "sync_gist_id": sync_gist_id or settings.get("sync_gist_id", ""),
        "sync_github_token": sync_github_token or settings.get("sync_github_token", ""),
        "sync_password": sync_password or settings.get("sync_password", ""),
    })
    # channel_niche / channel_name: None means "not set by caller, preserve
    # existing"; "" means "user explicitly cleared it" (defaults apply on read).
    if channel_niche is not None:
        settings["channel_niche"] = channel_niche
    if channel_name is not None:
        settings["channel_name"] = channel_name
    # AI provider fields: dict of exact values to set (blank = clear)
    if ai_settings is not None:
        settings.update(ai_settings)
    # Other managed fields (e.g. backend_url/backend_token): exact values to set
    if extra_settings is not None:
        settings.update(extra_settings)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)
    _schedule_sync_push()


DEFAULT_CHANNEL_NAME = "Sovereign Sessions"

# AI provider fields managed by the Settings form (posted on every save;
# blank values intentionally clear the key)
AI_SETTING_KEYS = ("ai_provider", "anthropic_model", "openai_key", "maple_key", "maple_url",
                   "lmstudio_url", "openai_model", "maple_model", "lmstudio_model",
                   "whisper_url", "whisper_model", "dictation_provider", "dictation_model")


def _ai_settings_from_form() -> dict:
    return {k: request.form.get(k, "").strip() for k in AI_SETTING_KEYS}


def get_channel_name() -> str:
    """Display name for the channel this planner is building for."""
    return (load_settings().get("channel_name") or "").strip() or DEFAULT_CHANNEL_NAME


@app.context_processor
def inject_channel_name():
    return {"channel_name": get_channel_name()}


def get_channel_niche() -> str:
    """Return the configured channel niche, or empty string to let brainstorm.py default apply."""
    return (load_settings().get("channel_niche") or "").strip()


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


def save_plans(data: list):
    _ensure_data_dir()
    with open(PLANS_FILE, "w") as f:
        json.dump(data, f)
    _schedule_sync_push()


CURRENT_PLAN_SCHEMA = 2


def migrate_plan(plan: dict) -> dict:
    """Migrate a plan to the current schema version."""
    v = plan.get("schema_version", 1)

    if v < 2:
        # v1 → v2: add full_plan and form_state from available fields
        if "full_plan" not in plan or not plan["full_plan"]:
            plan["full_plan"] = {
                "titles": plan.get("all_titles", []),
                "thumbnail_ideas": plan.get("thumbnail_ideas", []),
                "intro_hook": "",
                "outline": [],
                "tags": plan.get("tags", []),
                "description": "",
            }
        if "form_state" not in plan:
            plan["form_state"] = {
                "topic": plan.get("topic", ""),
                "video_type": plan.get("video_type", ""),
                "notes": "",
                "links": [],
                "competitors": [],
            }

    # Future migrations go here:
    # if v < 3:
    #     plan["new_field"] = default_value

    plan["schema_version"] = CURRENT_PLAN_SCHEMA
    return plan


def load_plans() -> list:
    if os.path.exists(PLANS_FILE):
        with open(PLANS_FILE) as f:
            plans = json.load(f)
        migrated = False
        for i, p in enumerate(plans):
            if p.get("schema_version", 1) < CURRENT_PLAN_SCHEMA:
                plans[i] = migrate_plan(p)
                migrated = True
        if migrated:
            save_plans(plans)
        return plans
    return []


# Load persisted data on startup (local files only — instant)
from sync import pull_from_gist, push_to_gist, is_sync_configured

_saved = load_settings()
playlist_info = {
    "playlist_id": _saved.get("playlist_id", ""),
    "google_key": _saved.get("google_key", ""),
}
video_cache = load_video_cache()
analytics_cache = load_analytics()
competitors_cache = load_competitors()
title_history = load_title_history()
plans_cache = load_plans()

# Heavy startup work (network calls) runs in background so the server starts fast
_btc_prices = {}
_snapshots = {}
_startup_done = threading.Event()

def _background_startup():
    global _btc_prices, _snapshots, video_cache, analytics_cache, competitors_cache, title_history, plans_cache
    try:
        # Sync pull — only auto-pull if a sync password is set (encrypted sync).
        # Without a password, auto-pull would overwrite local API keys with the
        # stripped Gist version. Users without a password can still pull manually.
        from sync import _get_sync_password
        _pw = _get_sync_password()
        if _pw:
            try:
                if pull_from_gist():
                    # Reload data that may have been updated by sync
                    video_cache = load_video_cache()
                    analytics_cache = load_analytics()
                    competitors_cache = load_competitors()
                    title_history = load_title_history()
                    plans_cache = load_plans()
                    _saved_inner = load_settings()
                    playlist_info["playlist_id"] = _saved_inner.get("playlist_id", "") or playlist_info["playlist_id"]
                    playlist_info["google_key"] = _saved_inner.get("google_key", "") or playlist_info["google_key"]
            except Exception as e:
                print(f"[Sync] Pull on startup failed: {e}")
        else:
            print("[Sync] Skipping auto-pull (no sync password set). Use Pull button in Settings.")

        # Auto-refresh competitors
        _gk = playlist_info.get("google_key", "")
        if _gk and competitors_cache:
            for _comp in competitors_cache:
                try:
                    _result = fetch_channel_videos(_comp["url"], _gk, max_videos=30)
                    _comp["videos"] = _result["videos"]
                    _comp["name"] = _result["channel_title"]
                except Exception:
                    pass
            save_competitors(competitors_cache)

        # Snapshots
        if video_cache or competitors_cache:
            _snapshots.update(record_snapshot(video_cache, competitors_cache))

        # BTC prices
        prices = fetch_btc_prices()
        if prices:
            _btc_prices.update(prices)
            _latest = sorted(_btc_prices.keys())[-1]
            print(f"[Market] BTC data loaded: {len(_btc_prices)} days, latest {_latest} = ${_btc_prices[_latest]:,.0f}")
        else:
            print("[Market] WARNING: Could not fetch BTC price data. Market features will show $0.")
    finally:
        _startup_done.set()
        print("[Startup] Background initialization complete.")

_startup_thread = threading.Thread(target=_background_startup, daemon=True)
_startup_thread.start()


@app.route("/")
def index():
    if not video_cache:
        return redirect(url_for("settings_page"))

    return render_template(
        "workspace.html",
        competitors=competitors_cache,
        chat_history=chat_history,
        backend_configured=backend_client.is_configured(load_settings()),
    )


@app.route("/planner")
def planner_redirect():
    """Compatibility shim: the Mac .app launcher polls /planner as its
    server-readiness check and requires a literal 200 (its curl doesn't
    follow redirects). Keep this alive until the bundle is rebuilt.
    Browsers that land here bounce to the workspace via meta refresh."""
    return '<!doctype html><meta http-equiv="refresh" content="0; url=/"><a href="/">BTC Sessions Planner</a>'


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
        sync_gist_id=settings.get("sync_gist_id", ""),
        sync_github_token=settings.get("sync_github_token", ""),
        sync_password=settings.get("sync_password", ""),
        sync_configured=is_sync_configured(),
        sponsor_template=settings.get("sponsor_template", ""),
        default_yt_tags=settings.get("default_yt_tags", ""),
        channel_niche=settings.get("channel_niche", ""),
        default_channel_niche=DEFAULT_CHANNEL_NICHE,
        channel_name_setting=settings.get("channel_name", ""),
        default_channel_name=DEFAULT_CHANNEL_NAME,
        backend_url=settings.get("backend_url", ""),
        backend_token=settings.get("backend_token", ""),
        backend_configured=backend_client.is_configured(settings),
        ai={k: settings.get(k, "") for k in AI_SETTING_KEYS},
        ai_defaults={
            "maple_url": llm.DEFAULT_MAPLE_URL,
            "lmstudio_url": llm.DEFAULT_LMSTUDIO_URL,
            "anthropic_model": llm.DEFAULT_ANTHROPIC_MODEL,
            "openai_model": llm.DEFAULT_OPENAI_MODEL,
            "maple_model": llm.DEFAULT_MAPLE_MODEL,
            "whisper_url": llm.DEFAULT_WHISPER_URL,
            "dictation_model": llm.DEFAULT_DICTATION_ANTHROPIC_MODEL,
        },
        provider_labels=llm.PROVIDER_LABELS,
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
    sync_password = request.form.get("sync_password", "").strip()
    channel_niche = request.form.get("channel_niche", "").strip()
    channel_name = request.form.get("channel_name", "").strip()

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
                      sync_gist_id, sync_github_token, sync_password,
                      channel_niche=channel_niche, channel_name=channel_name,
                      ai_settings=_ai_settings_from_form())
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
    sync_password = request.form.get("sync_password", "").strip()
    channel_niche = request.form.get("channel_niche", "").strip()
    channel_name = request.form.get("channel_name", "").strip()

    existing = load_settings()
    playlist_id = existing.get("playlist_id", "")
    if playlist_url:
        try:
            playlist_id = parse_playlist_id(playlist_url)
        except Exception:
            playlist_id = existing.get("playlist_id", "")

    save_settings(playlist_id, google_key, anthropic_key, playlist_url,
                  oauth_client_id, oauth_client_secret,
                  sync_gist_id, sync_github_token, sync_password,
                  channel_niche=channel_niche, channel_name=channel_name,
                  ai_settings=_ai_settings_from_form())

    return redirect(url_for("settings_page", success="Settings saved."))


@app.route("/api/list-models", methods=["POST"])
def api_list_models():
    """Model IDs available from an AI provider, for the Settings dropdowns.

    Unsaved credentials typed into the form are sent along and overlaid
    (non-empty values only, so a blank form field doesn't hide an
    env-var-configured key). Nothing is persisted.
    """
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "").strip()
    if provider not in llm.PROVIDERS:
        return jsonify({"ok": False, "error": "Unknown provider"}), 400
    settings = dict(load_settings())
    for key in ("anthropic_key", "openai_key", "maple_key", "maple_url", "lmstudio_url"):
        value = (data.get(key) or "").strip()
        if value:
            settings[key] = value
    try:
        return jsonify({"ok": True, "models": llm.list_models(provider, settings)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e) or type(e).__name__})


@app.route("/api/desc-template", methods=["GET", "POST"])
def api_desc_template():
    """Get or save the description defaults (sponsor block, default YT tags)."""
    settings = load_settings()
    if request.method == "GET":
        return jsonify({
            "sponsor_template": settings.get("sponsor_template", ""),
            "default_yt_tags": settings.get("default_yt_tags", ""),
        })

    data = request.get_json()
    # sponsor_template is legacy (superseded by sponsor-type library entries);
    # only touch it when explicitly sent so tag-only saves preserve it
    if "sponsor_template" in data:
        settings["sponsor_template"] = data.get("sponsor_template", "")
    settings["default_yt_tags"] = data.get("default_yt_tags", "")
    # Remove old field if migrating
    settings.pop("desc_template", None)
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)
    _schedule_sync_push()
    return jsonify({"ok": True})


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
    if not llm.any_configured(settings):
        return jsonify({"error": "No AI provider configured. Set one up in Settings."}), 400

    try:
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }

        # Gather real-time trend intelligence for the chat
        trend_intel_context = ""
        try:
            from trend_intel import gather_trend_intel, intel_to_prompt_context
            intel = gather_trend_intel()
            trend_intel_context = intel_to_prompt_context(intel)
        except Exception as e:
            print(f"[Chat] Trend intel unavailable: {e}")

        reply = chat_with_claude(user_message, video_cache, chat_history, settings, analytics_cache, competitors_cache, market_data=market_data, plan_state=plan_state, trend_intel_context=trend_intel_context, creator_niche=get_channel_niche(), lessons=settings.get("plan_lessons", []))

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

        # Check for standing-rule (lesson) blocks the model wants saved
        lessons_saved = []
        if "```plan_lesson" in reply:
            import re
            texts = []
            for block in re.findall(r"```plan_lesson\s*\n([\s\S]*?)```", reply):
                try:
                    text = (json.loads(block).get("text") or "").strip()
                    if text:
                        texts.append(text)
                except json.JSONDecodeError:
                    pass
            reply = re.sub(r"```plan_lesson\s*\n[\s\S]*?```", "", reply)
            reply = re.sub(r"\n{3,}", "\n\n", reply).strip()
            if texts:
                lessons_saved = _save_plan_lessons(texts, source="chat")

        chat_history.append({"role": "user", "content": user_message})
        chat_history.append({"role": "assistant", "content": reply})

        result = {"reply": reply}
        if plan_change:
            result["plan_change"] = plan_change
        if lessons_saved:
            result["lessons_saved"] = lessons_saved
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-plan", methods=["POST"])
def api_generate_plan():
    data = request.get_json()
    topic = data.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    video_type = data.get("video_type", "")
    links = data.get("links", [])
    competitors = data.get("competitors", [])
    notes = data.get("notes", "")

    settings = load_settings()
    if not llm.any_configured(settings):
        return jsonify({"error": "No AI provider configured. Set one up in Settings."}), 400

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

        settings = load_settings()

        plan = generate_video_plan(
            video_type=video_type,
            topic=topic,
            links=links,
            competitors=competitors,
            notes=notes,
            video_data=video_cache,
            analytics_data=analytics_cache,
            competitors_data=competitors_cache,
            settings=settings,
            market_data=market_data,
            trend_intel_context=trend_intel_context,
            title_history=title_history,
            sponsor_template=settings.get("sponsor_template", ""),
            default_yt_tags=settings.get("default_yt_tags", ""),
            sponsors=_resolve_sponsors(settings, data.get("sponsors")),
            creator_niche=settings.get("channel_niche", ""),
            affiliate_links=settings.get("affiliate_links", []),
            lessons=settings.get("plan_lessons", []),
        )
        plan["_scoring_ctx"] = _build_scoring_context()
        plan["_provider"] = llm.get_last_provider_used()
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

    # Incorporate keyword insights from post-mortems
    for plan in plans_cache:
        pm = plan.get("postmortem")
        if not pm:
            continue
        perf = plan.get("perf_ratio", 1.0) or 1.0
        for kw in pm.get("keyword_insights", []):
            kw_lower = kw.lower().strip()
            for w in kw_lower.split():
                w = w.strip("()[].,!?:;\"'")
                if len(w) > 2 and w not in stop_words:
                    # Boost keywords from outperforming plans, penalize from underperforming
                    weight = min(3.0, max(0.3, perf))
                    learned_keywords[w] = learned_keywords.get(w, 0) + weight

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


@app.route("/api/swap-title", methods=["POST"])
def api_swap_title():
    """Generate a single replacement title, avoiding duplicates of existing ones."""
    data = request.get_json()
    current_titles = data.get("current_titles", [])
    topic = data.get("topic", "")
    video_type = data.get("video_type", "")

    settings = load_settings()
    if not llm.any_configured(settings):
        return jsonify({"error": "No AI provider configured. Set one up in Settings."}), 400

    try:
        from brainstorm import swap_single_title
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        title = swap_single_title(
            current_titles=current_titles,
            topic=topic,
            video_type=video_type,
            video_data=video_cache,
            settings=settings,
            market_data=market_data,
            title_history=title_history,
            creator_niche=get_channel_niche(),
        )
        return jsonify({"title": title})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _resolve_sponsors(settings: dict, selected_urls) -> list:
    """Map the per-episode picker's selected URLs to sponsor library entries."""
    if not selected_urls:
        return []
    wanted = {u for u in selected_urls if u}
    return [l for l in settings.get("affiliate_links", [])
            if (l.get("type") or "affiliate") == "sponsor" and l.get("url") in wanted]


@app.route("/api/affiliate-links", methods=["GET"])
def api_get_affiliate_links():
    settings = load_settings()
    return jsonify({"links": settings.get("affiliate_links", [])})


@app.route("/api/affiliate-links/add", methods=["POST"])
def api_add_affiliate_link():
    data = request.get_json()
    title = (data.get("title") or "").strip()
    url = (data.get("url") or "").strip()
    link_type = data.get("type") if data.get("type") in ("affiliate", "sponsor") else "affiliate"
    blurb = (data.get("blurb") or "").strip()
    if not title or not url:
        return jsonify({"error": "Title and URL required."}), 400
    settings = load_settings()
    links = settings.get("affiliate_links", [])
    entry = {"title": title, "url": url, "type": link_type}
    if blurb:
        entry["blurb"] = blurb
    links.append(entry)
    settings["affiliate_links"] = links
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)
    _schedule_sync_push()
    return jsonify({"ok": True})


@app.route("/api/affiliate-links/remove", methods=["POST"])
def api_remove_affiliate_link():
    data = request.get_json()
    idx = data.get("index")
    settings = load_settings()
    links = settings.get("affiliate_links", [])
    if idx is None or idx < 0 or idx >= len(links):
        return jsonify({"error": "Invalid index."}), 400
    links.pop(idx)
    settings["affiliate_links"] = links
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)
    _schedule_sync_push()
    return jsonify({"ok": True})


def _save_plan_lessons(texts: list, source: str = "chat") -> list:
    """Append standing rules to settings, skipping duplicates.

    Returns the newly saved entries as [{"text", "index"}] so the UI can
    offer an undo by index.
    """
    from datetime import datetime
    settings = load_settings()
    lessons = settings.get("plan_lessons", [])
    existing = {(l.get("text") or "").strip().lower() for l in lessons}
    saved = []
    for text in texts:
        text = text.strip()
        if not text or text.lower() in existing:
            continue
        lessons.append({"text": text, "created_at": datetime.now().isoformat(), "source": source})
        existing.add(text.lower())
        saved.append({"text": text, "index": len(lessons) - 1})
    if saved:
        settings["plan_lessons"] = lessons
        _ensure_data_dir()
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f)
        _schedule_sync_push()
    return saved


@app.route("/api/lessons", methods=["GET"])
def api_get_lessons():
    settings = load_settings()
    return jsonify({"lessons": settings.get("plan_lessons", [])})


@app.route("/api/lessons/add", methods=["POST"])
def api_add_lesson():
    data = request.get_json()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Lesson text required."}), 400
    saved = _save_plan_lessons([text], source="manual")
    if not saved:
        return jsonify({"error": "That lesson is already saved."}), 400
    return jsonify({"ok": True, "saved": saved})


@app.route("/api/lessons/remove", methods=["POST"])
def api_remove_lesson():
    data = request.get_json()
    idx = data.get("index")
    text = (data.get("text") or "").strip()
    settings = load_settings()
    lessons = settings.get("plan_lessons", [])
    # Prefer matching by text — indexes shift when other lessons are removed
    if text:
        matches = [i for i, l in enumerate(lessons) if (l.get("text") or "").strip() == text]
        if not matches:
            return jsonify({"error": "Lesson not found."}), 400
        idx = matches[0]
    elif idx is None or idx < 0 or idx >= len(lessons):
        return jsonify({"error": "Invalid index."}), 400
    lessons.pop(idx)
    settings["plan_lessons"] = lessons
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)
    _schedule_sync_push()
    return jsonify({"ok": True})


@app.route("/api/search-tutorials", methods=["POST"])
def api_search_tutorials():
    """Search own channel videos by keyword — no AI, just fast text matching."""
    data = request.get_json()
    query = (data.get("query") or "").strip().lower()
    if not query:
        return jsonify({"results": []})

    keywords = query.split()
    results = []
    for v in video_cache:
        title_lower = v["title"].lower()
        # Score by how many keywords match
        matches = sum(1 for kw in keywords if kw in title_lower)
        if matches > 0:
            results.append({
                "video_id": v["video_id"],
                "title": v["title"],
                "url": f"https://youtube.com/watch?v={v['video_id']}",
                "view_count": v["view_count"],
                "matches": matches,
            })

    # Sort by match count then views
    results.sort(key=lambda r: (r["matches"], r["view_count"]), reverse=True)
    return jsonify({"results": results[:10]})


@app.route("/api/suggest-links", methods=["POST"])
def api_suggest_links():
    """Suggest relevant links for a plan from channel videos and the web."""
    data = request.get_json()
    topic = (data.get("topic") or "").strip()
    outline = data.get("outline", [])

    settings = load_settings()
    if not llm.any_configured(settings):
        return jsonify({"error": "No AI provider configured. Set one up in Settings."}), 400

    try:
        from brainstorm import suggest_plan_links
        affiliate_links = settings.get("affiliate_links", [])
        result = suggest_plan_links(
            topic=topic,
            outline=outline,
            video_data=video_cache,
            settings=settings,
            affiliate_links=affiliate_links,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/parse-notes", methods=["POST"])
def api_parse_notes():
    """Parse freeform notes into a structured plan."""
    data = request.get_json()
    notes = (data.get("notes") or "").strip()
    topic = (data.get("topic") or "").strip()
    video_type = data.get("video_type", "")
    existing_plan = data.get("existing_plan")

    if not notes:
        return jsonify({"error": "No notes to parse."}), 400

    settings = load_settings()
    if not llm.any_configured(settings):
        return jsonify({"error": "No AI provider configured. Set one up in Settings."}), 400

    try:
        from brainstorm import parse_notes_to_plan
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        result = parse_notes_to_plan(
            notes=notes,
            topic=topic,
            video_type=video_type,
            settings=settings,
            existing_plan=existing_plan,
            sponsor_template=settings.get("sponsor_template", ""),
            sponsors=_resolve_sponsors(settings, data.get("sponsors")),
            default_yt_tags=settings.get("default_yt_tags", ""),
            video_data=video_cache,
            market_data=market_data,
            competitors_data=competitors_cache,
            creator_niche=settings.get("channel_niche", ""),
            affiliate_links=settings.get("affiliate_links", []),
            lessons=settings.get("plan_lessons", []),
        )
        result["_scoring_ctx"] = _build_scoring_context()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transcribe", methods=["POST"])
def api_transcribe():
    """Transcribe dictated audio (local Whisper) and refine it into clean text."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio uploaded"}), 400
    f = request.files["audio"]
    mode = (request.form.get("mode") or "notes").strip()

    settings = load_settings()
    try:
        raw = llm.transcribe_audio(settings, f.read(), f.filename or "audio.webm",
                                   f.mimetype or "audio/webm")
        if not raw.strip():
            return jsonify({"raw": "", "refined": ""})
        # Refine when a chat provider is available; otherwise degrade to raw-only.
        refined = raw
        if llm.any_configured(settings):
            try:
                from brainstorm import refine_dictation
                refined = refine_dictation(settings, raw, mode) or raw
            except Exception as e:
                print(f"[Dictation] refine failed, returning raw transcript: {e}")
        return jsonify({"raw": raw, "refined": refined})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/regenerate-titles", methods=["POST"])
def api_regenerate_titles():
    """Generate new title suggestions for an existing plan."""
    data = request.get_json()
    topic = data.get("topic", "").strip()
    video_type = data.get("video_type", "")
    notes = data.get("notes", "")

    settings = load_settings()
    if not llm.any_configured(settings):
        return jsonify({"error": "No AI provider configured. Set one up in Settings."}), 400

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
            settings=settings,
            market_data=market_data,
            title_history=title_history,
            creator_niche=get_channel_niche(),
        )
        return jsonify({"titles": titles, "_scoring_ctx": _build_scoring_context()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scoring-context", methods=["GET"])
def api_scoring_context():
    """Return the current title scoring context."""
    return jsonify({"scoring_ctx": _build_scoring_context()})


@app.route("/api/score-titles", methods=["POST"])
def api_score_titles():
    """Have Claude score and critique title options."""
    data = request.get_json()
    titles = data.get("titles", [])
    topic = data.get("topic", "").strip()
    video_type = data.get("video_type", "")

    if not titles:
        return jsonify({"error": "No titles to score."}), 400

    settings = load_settings()
    if not llm.any_configured(settings):
        return jsonify({"error": "No AI provider configured. Set one up in Settings."}), 400

    try:
        from brainstorm import score_titles_ai
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        scores = score_titles_ai(
            titles=titles,
            topic=topic,
            video_type=video_type,
            video_data=video_cache,
            settings=settings,
            market_data=market_data,
            creator_niche=get_channel_niche(),
        )
        return jsonify({"scores": scores})
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


@app.route("/api/plans/save", methods=["POST"])
def api_save_plan():
    """Save or update a full plan for resuming and post-mortem tracking."""
    global plans_cache
    data = request.get_json()
    plan_data = data.get("plan", {})
    chosen_title = data.get("chosen_title", "")
    topic = data.get("topic", "")
    video_type = data.get("video_type", "")
    ai_score = data.get("ai_score")
    plan_id = data.get("plan_id")  # if updating an existing plan

    if not chosen_title:
        return jsonify({"error": "No title selected."}), 400

    from datetime import datetime

    # Full plan data for resuming later
    full_plan = {
        "titles": plan_data.get("titles", []),
        "thumbnail_ideas": plan_data.get("thumbnail_ideas", []),
        "intro_hook": plan_data.get("intro_hook", ""),
        "outline": plan_data.get("outline", []),
        "tags": plan_data.get("tags", []),
        "yt_tags_csv": plan_data.get("yt_tags_csv", ""),
        "links": plan_data.get("links", []),
        "description": plan_data.get("description", ""),
    }

    if plan_id:
        # Update existing plan
        plan = next((p for p in plans_cache if p["id"] == plan_id), None)
        if plan:
            plan["chosen_title"] = chosen_title
            plan["topic"] = topic
            plan["video_type"] = video_type
            plan["ai_score"] = ai_score
            plan["all_titles"] = plan_data.get("titles", [])
            plan["full_plan"] = full_plan
            plan["form_state"] = data.get("form_state", {})
            plan["updated_at"] = datetime.now().isoformat()[:10]
            plan["schema_version"] = CURRENT_PLAN_SCHEMA
            save_plans(plans_cache)
            return jsonify({"ok": True, "plan_id": plan_id})

    # Create new plan
    plan_entry = {
        "schema_version": CURRENT_PLAN_SCHEMA,
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "created_at": datetime.now().isoformat()[:10],
        "topic": topic,
        "video_type": video_type,
        "chosen_title": chosen_title,
        "all_titles": plan_data.get("titles", []),
        "ai_score": ai_score,
        "full_plan": full_plan,
        "form_state": data.get("form_state", {}),
        # Tracking fields — filled in when video is published
        "video_id": None,
        "published": False,
        "actual_title": None,
        "actual_views": None,
        "perf_ratio": None,
        "postmortem": None,
    }

    plans_cache.append(plan_entry)
    save_plans(plans_cache)
    return jsonify({"ok": True, "plan_id": plan_entry["id"]})


@app.route("/api/plans", methods=["GET"])
def api_get_plans():
    active = [p for p in plans_cache if not p.get("archived")]
    return jsonify({"plans": active})


@app.route("/api/plans/archived", methods=["GET"])
def api_get_archived_plans():
    archived = [p for p in plans_cache if p.get("archived")]
    return jsonify({"plans": archived})


@app.route("/api/plans/archive", methods=["POST"])
def api_archive_plan():
    global plans_cache
    data = request.get_json()
    plan_id = data.get("plan_id")
    plan = next((p for p in plans_cache if p["id"] == plan_id), None)
    if not plan:
        return jsonify({"error": "Plan not found."}), 404
    plan["archived"] = True
    save_plans(plans_cache)
    return jsonify({"ok": True})


@app.route("/api/plans/unarchive", methods=["POST"])
def api_unarchive_plan():
    global plans_cache
    data = request.get_json()
    plan_id = data.get("plan_id")
    plan = next((p for p in plans_cache if p["id"] == plan_id), None)
    if not plan:
        return jsonify({"error": "Plan not found."}), 404
    plan.pop("archived", None)
    save_plans(plans_cache)
    return jsonify({"ok": True})


@app.route("/api/plans/delete", methods=["POST"])
def api_delete_plan():
    global plans_cache
    data = request.get_json()
    plan_id = data.get("plan_id")
    plans_cache = [p for p in plans_cache if p["id"] != plan_id]
    save_plans(plans_cache)
    return jsonify({"ok": True})


@app.route("/api/plans/<plan_id>", methods=["GET"])
def api_get_plan(plan_id):
    plan = next((p for p in plans_cache if p["id"] == plan_id), None)
    if not plan:
        return jsonify({"error": "Plan not found."}), 404
    return jsonify({"plan": plan})


@app.route("/api/plans/<plan_id>/export", methods=["GET"])
def api_export_plan(plan_id):
    """Download a saved plan as a Markdown recording outline."""
    import io
    from plan_export import render_plan_markdown, export_filename

    plan = next((p for p in plans_cache if p["id"] == plan_id), None)
    if not plan:
        return jsonify({"error": "Plan not found."}), 404

    # WYSIWYG: the saved description already contains the synced LINKS block
    markdown = render_plan_markdown(plan, get_channel_name())
    buf = io.BytesIO(markdown.encode("utf-8"))
    return send_file(buf, mimetype="text/markdown", as_attachment=True,
                     download_name=export_filename(plan))


@app.route("/api/backend/save", methods=["POST"])
def api_backend_save():
    """Save the Umbrel backend URL + token, then test the connection."""
    data = request.get_json() or {}
    backend_url = (data.get("backend_url") or "").strip().rstrip("/")
    # Absent/blank token means "keep the current one" so re-testing the URL
    # doesn't require retyping the secret
    backend_token = (data.get("backend_token") or "").strip()
    if not backend_token:
        backend_token = (load_settings().get("backend_token") or "").strip()

    # Save first so a failed test doesn't force retyping everything
    save_settings(extra_settings={"backend_url": backend_url,
                                  "backend_token": backend_token})

    settings = load_settings()
    if not backend_client.is_configured(settings):
        return jsonify({"ok": False, "saved": True,
                        "error": "Backend disabled (URL or token blank)."})
    try:
        result = backend_client.health(settings)
        if result.get("ok"):
            return jsonify({"ok": True, "saved": True})
        return jsonify({"ok": False, "saved": True,
                        "error": f"Unexpected health response: {result}"})
    except backend_client.BackendError as e:
        return jsonify({"ok": False, "saved": True, "error": str(e)})


def _build_handoff_payload(plan: dict, version: int) -> dict:
    """Job payload for the Umbrel backend's recording_handoff worker."""
    from plan_export import render_plan_markdown as _render
    raw_plan = {k: v for k, v in plan.items() if k != "handoff"}
    return {
        "schema_version": 1,
        "job": {
            "task_type": "recording_handoff",
            "idempotency_key": f"plan-{plan['id']}-handoff-v{version}",
            "requested_output_schema": {"delivered_to": "string",
                                        "doc_url": "string?",
                                        "notes": "string?"},
        },
        "user_intent": {
            "topic": plan.get("topic", ""),
            "video_type": plan.get("video_type", ""),
        },
        "source_material": [
            {"type": "markdown", "title": "Rendered recording outline",
             "content": _render(plan, get_channel_name())},
            {"type": "json", "title": "Raw plan", "content": raw_plan},
        ],
        "backend_instructions": {"store_result": True, "handoff_allowed": True},
    }


@app.route("/api/plans/<plan_id>/handoff", methods=["POST"])
def api_submit_handoff(plan_id):
    """Send a saved plan to Hermes for recording handoff (async job)."""
    settings = load_settings()
    if not backend_client.is_configured(settings):
        return jsonify({"error": "Backend not configured. Set it up in Settings."}), 400

    plan = next((p for p in plans_cache if p["id"] == plan_id), None)
    if not plan:
        return jsonify({"error": "Plan not found."}), 404

    version = (plan.get("handoff") or {}).get("version", 0) + 1
    payload = _build_handoff_payload(plan, version)
    try:
        job = backend_client.submit_job(settings, payload)
    except backend_client.BackendError as e:
        return jsonify({"error": str(e)}), 502

    from datetime import datetime
    plan["handoff"] = {
        "job_id": job.get("job_id") or job.get("id"),
        "idempotency_key": payload["job"]["idempotency_key"],
        "version": version,
        "status": job.get("status", "queued"),
        "submitted_at": datetime.now().isoformat()[:19],
        "result": job.get("result"),
    }
    save_plans(plans_cache)
    return jsonify({"ok": True, "handoff": plan["handoff"]})


@app.route("/api/plans/<plan_id>/handoff", methods=["GET"])
def api_handoff_status(plan_id):
    """Poll the backend for the plan's handoff job status."""
    plan = next((p for p in plans_cache if p["id"] == plan_id), None)
    if not plan:
        return jsonify({"error": "Plan not found."}), 404
    handoff = plan.get("handoff")
    if not handoff or not handoff.get("job_id"):
        return jsonify({"error": "No handoff submitted for this plan."}), 404

    settings = load_settings()
    if not backend_client.is_configured(settings):
        return jsonify({"error": "Backend not configured."}), 400
    try:
        job = backend_client.get_job(settings, handoff["job_id"])
    except backend_client.BackendError as e:
        return jsonify({"error": str(e), "handoff": handoff}), 502

    status = job.get("status", handoff["status"])
    result = job.get("result")
    if status != handoff.get("status") or result != handoff.get("result"):
        handoff["status"] = status
        handoff["result"] = result
        save_plans(plans_cache)
    return jsonify({"ok": True, "handoff": handoff})


@app.route("/api/plans/link-video", methods=["POST"])
def api_link_video_to_plan():
    """Link a published video to a saved plan for post-mortem tracking."""
    global plans_cache
    data = request.get_json()
    plan_id = data.get("plan_id")
    video_id = data.get("video_id")

    plan = next((p for p in plans_cache if p["id"] == plan_id), None)
    if not plan:
        return jsonify({"error": "Plan not found."}), 404

    # Find the video in our cache
    video = next((v for v in video_cache if v["video_id"] == video_id), None)
    if not video:
        return jsonify({"error": "Video not found in playlist."}), 404

    avg_views = sum(v["view_count"] for v in video_cache) // len(video_cache) if video_cache else 1
    perf_ratio = round(video["view_count"] / avg_views, 2) if avg_views else 0

    plan["video_id"] = video_id
    plan["published"] = True
    plan["actual_title"] = video["title"]
    plan["actual_views"] = video["view_count"]
    plan["perf_ratio"] = perf_ratio
    plan["engagement_rate"] = video.get("engagement_rate", 0)
    plan["published_at"] = video.get("published_at", "")

    # Tag market phase
    tagged = tag_video_market_phase(dict(video), _btc_prices)
    plan["market_phase"] = tagged.get("_market_phase", "unknown")

    save_plans(plans_cache)
    return jsonify({"ok": True, "perf_ratio": perf_ratio})


@app.route("/api/plans/auto-match", methods=["POST"])
def api_auto_match_plans():
    """Try to auto-match unlinked plans to published videos by title similarity."""
    global plans_cache
    matched = 0
    avg_views = sum(v["view_count"] for v in video_cache) // len(video_cache) if video_cache else 1

    for plan in plans_cache:
        if plan.get("video_id"):
            continue  # already linked

        chosen = plan["chosen_title"].lower().strip()
        best_match = None
        best_ratio = 0

        for v in video_cache:
            vt = v["title"].lower().strip()
            if vt == chosen:
                best_match = v
                break
            saved_words = set(chosen.split())
            vid_words = set(vt.split())
            if not saved_words:
                continue
            overlap = len(saved_words & vid_words) / max(len(saved_words), len(vid_words))
            if overlap > best_ratio and overlap >= 0.6:
                best_ratio = overlap
                best_match = v

        if best_match:
            perf_ratio = round(best_match["view_count"] / avg_views, 2) if avg_views else 0
            plan["video_id"] = best_match["video_id"]
            plan["published"] = True
            plan["actual_title"] = best_match["title"]
            plan["actual_views"] = best_match["view_count"]
            plan["perf_ratio"] = perf_ratio
            plan["engagement_rate"] = best_match.get("engagement_rate", 0)
            plan["published_at"] = best_match.get("published_at", "")
            tagged = tag_video_market_phase(dict(best_match), _btc_prices)
            plan["market_phase"] = tagged.get("_market_phase", "unknown")
            matched += 1

    if matched:
        save_plans(plans_cache)
    return jsonify({"ok": True, "matched": matched})


@app.route("/api/plans/postmortem", methods=["POST"])
def api_plan_postmortem():
    """Generate an AI post-mortem for a linked plan."""
    data = request.get_json()
    plan_id = data.get("plan_id")

    plan = next((p for p in plans_cache if p["id"] == plan_id), None)
    if not plan or not plan.get("video_id"):
        return jsonify({"error": "Plan not found or no video linked."}), 400

    settings = load_settings()
    if not llm.any_configured(settings):
        return jsonify({"error": "No AI provider configured. Set one up in Settings."}), 400

    try:
        from brainstorm import generate_plan_postmortem
        result = generate_plan_postmortem(
            plan=plan,
            video_data=video_cache,
            settings=settings,
        )
        plan["postmortem"] = result
        save_plans(plans_cache)
        return jsonify({"ok": True, "postmortem": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@app.route("/api/competitors/toggle-own-channel", methods=["POST"])
def api_toggle_own_channel():
    global competitors_cache
    data = request.get_json()
    channel_id = data.get("channel_id", "")
    if not channel_id:
        return jsonify({"error": "channel_id required"}), 400
    for comp in competitors_cache:
        if comp["channel_id"] == channel_id:
            comp["is_own_channel"] = not comp.get("is_own_channel", False)
            save_competitors(competitors_cache)
            return jsonify({"ok": True, "is_own_channel": comp["is_own_channel"]})
    return jsonify({"error": "Channel not found"}), 404


@app.route("/api/competitors/set-own-role", methods=["POST"])
def api_set_own_role():
    """Set a tracked channel's role: '' (competitor), 'legacy' (previous main
    channel), or 'current' (the new channel these plans are for)."""
    global competitors_cache
    data = request.get_json()
    channel_id = data.get("channel_id", "")
    role = data.get("role", "")
    if not channel_id:
        return jsonify({"error": "channel_id required"}), 400
    if role not in ("", "legacy", "current"):
        return jsonify({"error": "role must be '', 'legacy', or 'current'"}), 400
    for comp in competitors_cache:
        if comp["channel_id"] == channel_id:
            comp["own_role"] = role
            # Keep the old boolean coherent for anything still reading it
            comp["is_own_channel"] = bool(role)
            save_competitors(competitors_cache)
            return jsonify({"ok": True, "own_role": role})
    return jsonify({"error": "Channel not found"}), 404


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
    if not llm.any_configured(settings):
        return jsonify({"error": "No AI provider configured. Set one up in Settings."}), 400

    if not video_cache:
        return jsonify({"error": "No videos loaded. Fetch your playlist first."}), 400

    # Accept exclude_handles from client to get fresh suggestions on refresh
    body = request.get_json(silent=True) or {}
    exclude_handles = body.get("exclude_handles", [])

    # Get creator's own channel identity to exclude from suggestions
    creator_channel = settings.get("playlist_url", "").strip()
    # If it's a full URL, try to extract the handle portion
    if "/" in creator_channel:
        parts = creator_channel.rstrip("/").split("/")
        for p in reversed(parts):
            if p.startswith("@"):
                creator_channel = p
                break

    try:
        market_data = {
            "info": get_current_market_info(_btc_prices),
            "summary": get_market_summary(_btc_prices, video_cache),
        }
        result = suggest_channels(
            video_data=video_cache,
            competitors_data=competitors_cache,
            settings=settings,
            market_data=market_data,
            exclude_handles=exclude_handles,
            creator_channel=creator_channel,
            creator_niche=settings.get("channel_niche", ""),
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


@app.route("/api/search-channels", methods=["POST"])
def api_search_channels():
    settings = load_settings()
    google_key = settings.get("google_key", "") or os.environ.get("GOOGLE_API_KEY", "")
    if not google_key:
        return jsonify({"error": "Google API key not configured."}), 400

    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Search query is required."}), 400

    try:
        from youtube import search_channels
        results = search_channels(query, google_key, max_results=8)
        # Mark channels already tracked
        tracked_ids = {c["channel_id"] for c in competitors_cache}
        for ch in results:
            ch["already_tracked"] = ch["channel_id"] in tracked_ids
        return jsonify({"channels": results})
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

        # Auto-fetch retention curves for top 15 videos
        top_vid_ids = [tv["video_id"] for tv in analytics_cache.get("top_videos", [])[:15]]
        if top_vid_ids:
            try:
                curves = fetch_retention_curves(top_vid_ids)
                analytics_cache["retention_curves"] = curves
            except Exception as e:
                print(f"[Analytics] Auto retention fetch failed: {e}")

        save_analytics(analytics_cache)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fetch-retention", methods=["POST"])
def api_fetch_retention():
    """Manually fetch retention curves for selected videos."""
    global analytics_cache
    data = request.get_json()
    video_ids = data.get("video_ids", [])
    if not video_ids:
        return jsonify({"error": "No video IDs provided"}), 400

    try:
        curves = fetch_retention_curves(video_ids)
        # Merge into existing retention data
        existing = analytics_cache.get("retention_curves", {})
        existing.update(curves)
        analytics_cache["retention_curves"] = existing
        save_analytics(analytics_cache)

        # Count how many had data
        fetched = sum(1 for v in curves.values() if v)
        return jsonify({"ok": True, "fetched": fetched, "total": len(video_ids)})
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
    password = data.get("password", "")
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
    if password:
        settings["sync_password"] = password
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
    password = data.get("password", "")
    if not token or not gist_id:
        return jsonify({"error": "Both token and gist_id are required"}), 400

    # Save sync config
    settings = load_settings()
    settings["sync_gist_id"] = gist_id
    settings["sync_github_token"] = token
    if password:
        settings["sync_password"] = password
    _ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

    # Pull data from the existing Gist
    global video_cache, analytics_cache, competitors_cache, title_history, plans_cache
    pull_from_gist()
    video_cache = load_video_cache()
    analytics_cache = load_analytics()
    competitors_cache = load_competitors()
    title_history = load_title_history()
    plans_cache = load_plans()

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
    """Update the GitHub token, Gist ID, and/or sync password."""
    data = request.get_json()
    token = data.get("token", "").strip()
    gist_id = data.get("gist_id", "").strip()
    password = data.get("password", "").strip()
    if not token and not gist_id and not password:
        return jsonify({"error": "Enter a token, Gist ID, or password to update"}), 400
    settings = load_settings()
    if token:
        settings["sync_github_token"] = token
    if gist_id:
        settings["sync_gist_id"] = gist_id
    if password:
        settings["sync_password"] = password
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
    from sync import get_last_sync_error
    detail = get_last_sync_error() or "Unknown error"
    return jsonify({"ok": False, "error": f"Push failed: {detail}"}), 500


@app.route("/api/sync/pull", methods=["POST"])
def api_sync_pull():
    """Manually trigger a sync pull."""
    if not is_sync_configured():
        return jsonify({"error": "Sync not configured"}), 400
    global video_cache, analytics_cache, competitors_cache, title_history, plans_cache
    updated = pull_from_gist()
    if updated:
        video_cache = load_video_cache()
        analytics_cache = load_analytics()
        competitors_cache = load_competitors()
        title_history = load_title_history()
        plans_cache = load_plans()
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
        TITLE_HISTORY_FILE, PLANS_FILE,
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
                "plans.json",
            }
            for name in zf.namelist():
                if name in allowed:
                    zf.extract(name, DATA_DIR)

        # Reload in-memory caches
        global video_cache, analytics_cache, competitors_cache, title_history, plans_cache
        video_cache = load_video_cache()
        analytics_cache = load_analytics()
        competitors_cache = load_competitors()
        title_history = load_title_history()
        plans_cache = load_plans()

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
    # Set PLANNER_HOST=0.0.0.0 to reach the app from a phone/tablet on the same
    # network (http://<machine-ip>:5000). Default stays local-only.
    host = os.environ.get("PLANNER_HOST", "127.0.0.1")
    port = int(os.environ.get("PLANNER_PORT", "5000"))
    app.run(debug=True, host=host, port=port, use_reloader=use_reloader)
