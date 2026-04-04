import os
import json
from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from dotenv import load_dotenv
from youtube import fetch_playlist_videos, parse_playlist_id, fetch_channel_videos
from brainstorm import chat_with_claude, generate_video_plan, analyze_competitor_video, analyze_own_video
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

# In-memory store
video_cache = []
chat_history = []
playlist_info = {}
analytics_cache = {}
competitors_cache = []  # list of {"name", "url", "category", "channel_id", "videos": [...]}


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def save_settings(playlist_id="", google_key="", anthropic_key="", playlist_url="",
                   oauth_client_id="", oauth_client_secret=""):
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
    }
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}


def save_video_cache(videos: list):
    _ensure_data_dir()
    with open(CACHE_FILE, "w") as f:
        json.dump(videos, f)


def load_video_cache() -> list:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return []


def save_analytics(data: dict):
    _ensure_data_dir()
    with open(ANALYTICS_FILE, "w") as f:
        json.dump(data, f)


def load_analytics() -> dict:
    if os.path.exists(ANALYTICS_FILE):
        with open(ANALYTICS_FILE) as f:
            return json.load(f)
    return {}


def save_competitors(data: list):
    _ensure_data_dir()
    with open(COMPETITORS_FILE, "w") as f:
        json.dump(data, f)


def load_competitors() -> list:
    if os.path.exists(COMPETITORS_FILE):
        with open(COMPETITORS_FILE) as f:
            return json.load(f)
    return []


# Load persisted data on startup
_saved = load_settings()
playlist_info = {
    "playlist_id": _saved.get("playlist_id", ""),
    "google_key": _saved.get("google_key", ""),
}
video_cache = load_video_cache()
analytics_cache = load_analytics()
competitors_cache = load_competitors()

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


@app.route("/")
def index():
    if not video_cache:
        return redirect(url_for("settings_page"))

    sorted_videos = sorted(video_cache, key=lambda v: v["view_count"], reverse=True)
    total_views = sum(v["view_count"] for v in video_cache)
    avg_engagement = round(sum(v["engagement_rate"] for v in video_cache) / len(video_cache), 2)

    return render_template(
        "workspace.html",
        videos=sorted_videos,
        total_views=total_views,
        avg_engagement=avg_engagement,
        competitors=competitors_cache,
        chat_history=chat_history,
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
    )


@app.route("/fetch", methods=["POST"])
def fetch():
    global video_cache, chat_history

    playlist_url = request.form.get("playlist_url", "").strip()
    google_key = request.form.get("google_key", "").strip()
    anthropic_key = request.form.get("anthropic_key", "").strip()
    oauth_client_id = request.form.get("oauth_client_id", "").strip()
    oauth_client_secret = request.form.get("oauth_client_secret", "").strip()

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
                      oauth_client_id, oauth_client_secret)
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

    settings = load_settings()
    api_key = settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured. Go back to the home page to set it."}), 400

    try:
        reply = chat_with_claude(user_message, video_cache, chat_history, api_key, analytics_cache, competitors_cache)
        chat_history.append({"role": "user", "content": user_message})
        chat_history.append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trends")
def trends_page():
    if not video_cache:
        return redirect(url_for("index"))
    trends_data = analyze_trends(video_cache)
    return render_template("trends.html", trends=trends_data, total_videos=len(video_cache))


@app.route("/planner")
def planner():
    return render_template("planner.html")


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

    try:
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
        )
        return jsonify(plan)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/competitors")
def competitors_page():
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
        except Exception as e:
            errors.append(f"{comp['name']}: {e}")

    save_competitors(competitors_cache)
    if errors:
        return jsonify({"ok": True, "warnings": errors})
    return jsonify({"ok": True})


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
        analysis = analyze_own_video(
            video=video,
            video_data=video_cache,
            api_key=api_key,
            analytics_data=analytics_cache,
            competitors_data=competitors_cache,
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
        analysis = analyze_competitor_video(
            video=video,
            competitor=competitor,
            video_data=video_cache,
            api_key=api_key,
            analytics_data=analytics_cache,
        )
        return jsonify(analysis)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/update", methods=["POST"])
def update_app():
    import subprocess
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "claude/youtube-planning-app-B52L2"],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(os.path.abspath(__file__)),
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
