import os
from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from dotenv import load_dotenv
from youtube import fetch_playlist_videos, parse_playlist_id
from brainstorm import chat_with_claude

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

# In-memory store (single-user tool)
video_cache = []
chat_history = []
playlist_info = {}  # stores playlist_id and google_key for refresh


@app.route("/")
def index():
    return render_template(
        "index.html",
        google_key=os.environ.get("GOOGLE_API_KEY", ""),
        anthropic_key=os.environ.get("ANTHROPIC_API_KEY", ""),
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
            "index.html",
            error="Playlist URL and Google API key are required.",
            google_key=google_key,
            anthropic_key=anthropic_key,
            has_videos=False,
        )

    # Store Anthropic key in session
    if anthropic_key:
        session["anthropic_key"] = anthropic_key

    try:
        playlist_id = parse_playlist_id(playlist_url)
        video_cache = fetch_playlist_videos(playlist_id, google_key)
        playlist_info["playlist_id"] = playlist_id
        playlist_info["google_key"] = google_key
        chat_history = []  # Reset chat on new playlist load
    except Exception as e:
        return render_template(
            "index.html",
            error=f"Failed to fetch playlist: {e}",
            google_key=google_key,
            anthropic_key=anthropic_key,
            has_videos=False,
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

    api_key = session.get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured. Go back to the home page to set it."}), 400

    try:
        reply = chat_with_claude(user_message, video_cache, chat_history, api_key)
        chat_history.append({"role": "user", "content": user_message})
        chat_history.append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/refresh", methods=["POST"])
def refresh():
    global video_cache
    if not playlist_info.get("playlist_id") or not playlist_info.get("google_key"):
        return jsonify({"error": "No playlist loaded. Go back to the home page to load one."}), 400
    try:
        video_cache = fetch_playlist_videos(playlist_info["playlist_id"], playlist_info["google_key"])
        return jsonify({"ok": True, "count": len(video_cache)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/clear-chat", methods=["POST"])
def clear_chat():
    global chat_history
    chat_history = []
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
