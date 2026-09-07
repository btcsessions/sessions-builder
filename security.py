"""Protect the local browser app without changing the Dock launch workflow."""
import hashlib
import ipaddress
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from flask import jsonify, redirect, render_template_string, request, session
from storage import atomic_text


def _loopback(value):
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value == "localhost"


def configure_security(app, data_dir):
    secret_path = Path(data_dir) / ".session-secret"
    if not secret_path.exists():
        atomic_text(secret_path, secrets.token_hex(32))
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secret_path.read_text().strip()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      MAX_CONTENT_LENGTH=100 * 1024 * 1024)
    # Existing installations predate owner-only storage.
    for path in Path(data_dir).glob("*.json*"):
        if path.is_file():
            path.chmod(0o600)

    @app.before_request
    def protect_request():
        host = urlsplit(request.host_url).hostname or ""
        allowed = {h.strip() for h in os.environ.get("PLANNER_ALLOWED_HOSTS", "").split(",") if h.strip()}
        try:
            ipaddress.ip_address(host)
            valid_host = True
        except ValueError:
            valid_host = host == "localhost" or host in allowed
        if not valid_host:
            return jsonify(error="Unrecognized planner host"), 403

        token = os.environ.get("PLANNER_ACCESS_TOKEN", "")
        bearer = request.headers.get("Authorization", "")
        authorized_bearer = bool(token) and secrets.compare_digest(bearer, "Bearer " + token)
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            return jsonify(error="Cross-origin requests are not allowed"), 403
        if request.headers.get("Sec-Fetch-Site") == "cross-site" and request.endpoint != "oauth_callback":
            return jsonify(error="Open the planner directly before continuing"), 403
        if request.method not in ("GET", "HEAD", "OPTIONS") and not authorized_bearer:
            if not origin and request.headers.get("Sec-Fetch-Site") != "same-origin":
                return jsonify(error="This action must come from the planner page. Refresh and retry."), 403

        # Loopback access stays password-free. LAN access is opt-in and authenticated.
        if not _loopback(request.remote_addr or ""):
            if not token:
                return jsonify(error="Network access requires PLANNER_ACCESS_TOKEN on the host machine"), 403
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            if not authorized_bearer and session.get("planner_access") != token_hash:
                if request.endpoint != "planner_login":
                    if request.method == "GET" and not request.path.startswith("/api/"):
                        return redirect("/login")
                    return jsonify(error="Sign in to the planner on this device first"), 401

    @app.route("/login", methods=["GET", "POST"])
    def planner_login():
        error = ""
        if request.method == "POST":
            token = os.environ.get("PLANNER_ACCESS_TOKEN", "")
            if token and secrets.compare_digest(request.form.get("token", ""), token):
                session["planner_access"] = hashlib.sha256(token.encode()).hexdigest()
                return redirect("/")
            error = "Incorrect access token."
        return render_template_string('''<!doctype html><title>Planner sign in</title>
            <h1>Planner sign in</h1><p>{{ error }}</p>
            <form method="post"><label>Access token <input type="password" name="token" required></label>
            <button>Sign in</button></form>''', error=error)

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        return response
