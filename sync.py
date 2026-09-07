"""Sync app data to/from a private GitHub Gist for multi-machine use.

Requires a GitHub personal access token with 'gist' scope and a Gist ID.
Stores each data file as a separate file in the Gist.

All data files are encrypted when a sync password is set. Legacy backups remain
readable through explicit restore; startup never replaces local work.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import threading
from storage import DATA_LOCK, read_json, restore_documents, validate_document
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

DATA_DIR = os.environ.get("PLANNER_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_REMOTE_LOCK = threading.RLock()

# Files to sync
SYNC_FILES = [
    "settings.json",
    "videos.json",
    "analytics.json",
    "competitors.json",
    "video_categories.json",
    "btc_prices.json",
    "snapshots.json",
    "oauth_token.json",
    "nostr_history.json",
    "title_history.json",
    "plans.json",
]

# Don't sync trend_intel.json — it's a cache that auto-refreshes

# Keys that are local-only (never pushed to Gist, preserved on pull)
LOCAL_ONLY_KEYS = ("sync_gist_id", "sync_github_token", "sync_password",
                   "backend_url", "backend_token")

# Keys that contain secrets — stripped from unencrypted pushes, and preserved
# on pull from unencrypted Gist (since the Gist won't have them)
SECRET_KEYS = ("google_key", "anthropic_key", "oauth_client_id", "oauth_client_secret",
               "openai_key", "maple_key", "vidiq_mcp_key")


def _get_sync_config() -> tuple[str, str]:
    """Read Gist ID and GitHub token from settings or env."""
    settings_path = os.path.join(DATA_DIR, "settings.json")
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    gist_id = settings.get("sync_gist_id", "") or os.environ.get("SYNC_GIST_ID", "")
    gh_token = settings.get("sync_github_token", "") or os.environ.get("SYNC_GITHUB_TOKEN", "")
    return gist_id, gh_token


def _get_sync_password() -> str:
    """Read the sync encryption password from settings."""
    settings_path = os.path.join(DATA_DIR, "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                return json.load(f).get("sync_password", "")
        except (json.JSONDecodeError, IOError):
            pass
    return ""


def is_sync_configured() -> bool:
    """Check if sync is set up."""
    gist_id, gh_token = _get_sync_config()
    return bool(gist_id and gh_token)


# --- Encryption helpers ---

def _derive_key(password: str) -> bytes:
    """Derive a 32-byte Fernet key from a password using PBKDF2."""
    # Fixed salt — all machines with the same password get the same key
    salt = b"sessions-builder-sync-v1"
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return base64.urlsafe_b64encode(dk)


def _encrypt(plaintext: str, password: str) -> str:
    """Encrypt a string with the sync password. Returns base64 ciphertext."""
    from cryptography.fernet import Fernet
    key = _derive_key(password)
    f = Fernet(key)
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def _decrypt(ciphertext: str, password: str) -> str | None:
    """Decrypt a string with the sync password. Returns None on failure."""
    from cryptography.fernet import Fernet, InvalidToken
    key = _derive_key(password)
    f = Fernet(key)
    try:
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception) as e:
        print(f"[Sync] Decryption failed: {e}")
        return None


_last_sync_error = ""

def _gist_api(method: str, gist_id: str, token: str, body: dict = None) -> dict | None:
    """Make a GitHub Gist API request."""
    global _last_sync_error
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "sessions-builder-sync",
    }
    data = None
    if body:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    try:
        req = Request(url, data=data, headers=headers, method=method)
        with urlopen(req, timeout=30) as resp:
            _last_sync_error = ""
            return json.loads(resp.read().decode())
    except HTTPError as e:
        _last_sync_error = f"GitHub API {e.code} {e.reason}"
        print(f"[Sync] GitHub API error: {e.code} {e.reason}")
        try:
            err_body = e.read().decode()
            print(f"[Sync] Response: {err_body[:200]}")
            _last_sync_error += f" — {err_body[:200]}"
        except Exception:
            pass
        return None
    except Exception as e:
        _last_sync_error = str(e)
        print(f"[Sync] Request failed: {e}")
        return None


def get_last_sync_error() -> str:
    return _last_sync_error


def _remote_content(entry):
    if entry.get("truncated"):
        # Never install GitHub's truncated preview as a complete data file.
        from urllib.parse import urlsplit
        url = entry.get("raw_url", "")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "gist.githubusercontent.com":
            raise ValueError("Backup has an invalid raw download URL")
        with urlopen(Request(url, headers={"User-Agent": "sessions-builder-sync"}), timeout=30) as response:
            content = response.read(50 * 1024 * 1024 + 1)
        if len(content) > 50 * 1024 * 1024:
            raise ValueError("Backup file is too large")
        return content.decode("utf-8")
    return entry.get("content", "")


def _local_contents():
    from pathlib import Path
    return {name: (Path(DATA_DIR) / name).read_text(encoding="utf-8")
            if (Path(DATA_DIR) / name).exists() else None for name in SYNC_FILES}


def pull_from_gist(on_restore=None) -> bool:
    """Explicit restore only; validate all files and retain a recovery snapshot."""
    global _last_sync_error
    with _REMOTE_LOCK:
        gist_id, token = _get_sync_config()
        if not gist_id or not token:
            _last_sync_error = "Sync is not configured"
            return False
        password = _get_sync_password()
        with DATA_LOCK:
            before = _local_contents()
        gist = _gist_api("GET", gist_id, token)
        if gist is None:
            return False
        try:
            documents = {}
            files = gist.get("files", {})
            for name in SYNC_FILES:
                encrypted = name + ".enc" in files
                entry = files.get(name + ".enc") if encrypted else files.get(name)
                if not entry:
                    continue
                content = _remote_content(entry)
                if encrypted:
                    if not password:
                        raise ValueError("Enter the backup password before restoring")
                    content = _decrypt(content, password)
                    if content is None:
                        raise ValueError("Cannot decrypt backup; check the sync password. Nothing was restored.")
                value = json.loads(content)
                validate_document(name, value)
                if name == "settings.json":
                    local = json.loads(before[name] or "{}")
                    for key in LOCAL_ONLY_KEYS:
                        value.pop(key, None)
                        if key in local:
                            value[key] = local[key]
                    if not encrypted:
                        for key in SECRET_KEYS:
                            if key in local and not value.get(key):
                                value[key] = local[key]
                if value != json.loads(before[name] or "null"):
                    documents[name] = value
            with DATA_LOCK:
                if _local_contents() != before:
                    raise ValueError("Local data changed during download. Retry the restore when editing is finished.")
                if documents:
                    restore_documents(DATA_DIR, documents)
                    if on_restore:
                        on_restore()
            _last_sync_error = ""
            return bool(documents)
        except (ValueError, OSError) as exc:
            _last_sync_error = str(exc)
            return False


def push_to_gist() -> bool:
    """Snapshot locally, then publish. Encrypt all files when a password exists."""
    global _last_sync_error
    with _REMOTE_LOCK:
        gist_id, token = _get_sync_config()
        if not gist_id or not token:
            _last_sync_error = "Sync is not configured"
            return False
        password = _get_sync_password()
        try:
            files = {"oauth_token.json": None}
            with DATA_LOCK:
                for name, content in _local_contents().items():
                    if content is None:
                        continue
                    value = json.loads(content)  # Fail closed: never upload corrupt settings.
                    validate_document(name, value)
                    if name == "settings.json":
                        for key in LOCAL_ONLY_KEYS:
                            value.pop(key, None)
                    if password:
                        files[name + ".enc"] = {"content": _encrypt(json.dumps(value), password)}
                        files[name] = None  # Remove plaintext from the current Gist revision.
                    elif name == "oauth_token.json":
                        files[name] = None  # OAuth credentials are never backed up unencrypted.
                    else:
                        if name == "settings.json":
                            for key in SECRET_KEYS:
                                value.pop(key, None)
                        files[name] = {"content": json.dumps(value)}
            if not any(value is not None for value in files.values()):
                _last_sync_error = "No data files to back up"
                return False
            result = _gist_api("PATCH", gist_id, token, {"files": files})
            return result is not None
        except (ValueError, OSError) as exc:
            _last_sync_error = str(exc)
            return False


def create_sync_gist(token: str, description: str = "BTC Sessions Planner - Data Sync") -> str | None:
    """Create a new private Gist for syncing. Returns the Gist ID."""
    url = "https://api.github.com/gists"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "sessions-builder-sync",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "description": description,
        "public": False,
        "files": {
            "README.md": {
                "content": "# BTC Sessions Planner - Data Sync\n\nThis Gist stores synced app data. Do not edit manually.\n"
            }
        },
    }).encode("utf-8")

    try:
        req = Request(url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        gist_id = data.get("id")
        print(f"[Sync] Created new Gist: {gist_id}")
        return gist_id
    except Exception as e:
        print(f"[Sync] Failed to create Gist: {e}")
        return None
