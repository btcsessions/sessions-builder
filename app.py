import os
import json
from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from dotenv import load_dotenv
from youtube import fetch_playlist_videos, parse_playlist_id
from brainstorm import chat_with_claude, generate_video_plan

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "yt-planner-secret-key")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
CACHE_FILE = os.path.join(DATA_DIR, "videos.json")

# In-memory store
video_cache = []
chat_history = []
playlist_info = {}


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def save_settings(playlist_id="", google_key="", anthropic_key="", playlist_url=""):
    _ensure_data_dir()
    settings = {
        "playlist_id": playlist_id,
        "playlist_url": playlist_url,
        "google_key": google_key,
        "anthropic_key": anthropic_key,
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


# Load persisted data on startup
_saved = load_settings()
playlist_info = {
    "playlist_id": _saved.get("playlist_id", ""),
    "google_key": _saved.get("google_key", ""),
}
video_cache = load_video_cache()


@app.route("/")
def index():
    if video_cache:
        return redirect(url_for("dashboard"))
    return redirect(url_for("settings_page"))


@app.route("/settings")
def settings_page():
    settings = load_settings()
    return render_template(
        "settings.html",
        google_key=settings.get("google_key", "") or os.environ.get("GOOGLE_API_KEY", ""),
        anthropic_key=settings.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", ""),
        playlist_url=settings.get("playlist_url", ""),
        has_videos=len(video_cache) > 0,
    )


@app.route("/fetch", methods=["POST"])
def fetch():
    global video_cache, chat_history

    playlist_url = request.form.get("playlist_url", "").strip()
    google_key = request.form.get("google_key", "").strip()
    anthropic_key = request.form.get("anthropic_key", "").strip()

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
        save_settings(playlist_id, google_key, anthropic_key, playlist_url)
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
        reply = chat_with_claude(user_message, video_cache, chat_history, api_key)
        chat_history.append({"role": "user", "content": user_message})
        chat_history.append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
            api_key=api_key,
        )
        return jsonify(plan)
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
