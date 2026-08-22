"""Sync app data to/from a private GitHub Gist for multi-machine use.

Requires a GitHub personal access token with 'gist' scope and a Gist ID.
Stores each data file as a separate file in the Gist.

settings.json is encrypted before pushing to prevent GitHub secret scanning
from detecting and revoking API keys. A sync password (same on all machines)
is used for encryption.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

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


def pull_from_gist() -> bool:
    """Pull latest data from the Gist. Returns True if data was updated."""
    gist_id, token = _get_sync_config()
    if not gist_id or not token:
        return False

    password = _get_sync_password()

    print("[Sync] Pulling data from Gist...")
    gist = _gist_api("GET", gist_id, token)
    if not gist:
        print("[Sync] Pull failed.")
        return False

    files = gist.get("files", {})
    if not files:
        print("[Sync] Gist is empty, nothing to pull.")
        return False

    os.makedirs(DATA_DIR, exist_ok=True)
    updated = 0

    for filename in SYNC_FILES:
        settings_encrypted = False

        # For settings.json, check for encrypted version first
        if filename == "settings.json":
            if "settings.json.enc" in files and password:
                enc_content = files["settings.json.enc"].get("content", "")
                if enc_content:
                    decrypted = _decrypt(enc_content, password)
                    if decrypted:
                        content = decrypted
                        settings_encrypted = True
                        print("[Sync] Decrypted settings.json from Gist.")
                    else:
                        print("[Sync] WARNING: Could not decrypt settings.json — wrong password?")
                        continue
                else:
                    continue
            elif filename in files:
                # Fallback: unencrypted settings.json (legacy or no password)
                content = files[filename].get("content", "")
                if not content:
                    continue
            else:
                continue
        else:
            if filename not in files:
                continue
            content = files[filename].get("content", "")
            if not content:
                continue

        local_path = os.path.join(DATA_DIR, filename)

        # Compare with local — skip if identical
        local_content = ""
        if os.path.exists(local_path):
            try:
                with open(local_path) as f:
                    local_content = f.read()
            except IOError:
                pass

        if content.strip() == local_content.strip():
            continue

        # For settings.json, preserve local-only keys (always) and
        # secret keys (only when pulling unencrypted, since those are
        # stripped from the Gist and the local copy has the real values)
        if filename == "settings.json":
            try:
                local_settings = json.loads(local_content) if local_content.strip() else {}
                gist_settings = json.loads(content)
                # Always preserve local-only keys
                for key in LOCAL_ONLY_KEYS:
                    if key in local_settings:
                        gist_settings[key] = local_settings[key]
                # If unencrypted, also preserve secret keys (they were stripped)
                if not settings_encrypted:
                    for key in SECRET_KEYS:
                        if key in local_settings and not gist_settings.get(key):
                            gist_settings[key] = local_settings[key]
                content = json.dumps(gist_settings)
            except (json.JSONDecodeError, ValueError):
                pass

        # Write the Gist version locally
        with open(local_path, "w") as f:
            f.write(content)
        updated += 1

    if updated:
        print(f"[Sync] Pulled {updated} file(s) from Gist.")
    else:
        print("[Sync] Already up to date.")
    return updated > 0


def push_to_gist() -> bool:
    """Push current data files to the Gist. Returns True on success."""
    global _last_sync_error
    gist_id, token = _get_sync_config()
    if not gist_id or not token:
        print(f"[Sync] Push skipped — gist_id={'set' if gist_id else 'MISSING'}, token={'set' if token else 'MISSING'}")
        return False
    print(f"[Sync] Config: gist_id={gist_id[:8]}..., token={token[:8]}...")

    password = _get_sync_password()

    files = {}
    for filename in SYNC_FILES:
        local_path = os.path.join(DATA_DIR, filename)
        if os.path.exists(local_path):
            try:
                with open(local_path) as f:
                    content = f.read()
                if not content.strip():
                    continue

                if filename == "settings.json":
                    # Strip local-only keys (they don't belong in the Gist)
                    try:
                        settings_data = json.loads(content)
                        for key in LOCAL_ONLY_KEYS:
                            settings_data.pop(key, None)
                        content = json.dumps(settings_data)
                    except (json.JSONDecodeError, ValueError):
                        pass

                    if password:
                        # Encrypt settings and push as settings.json.enc
                        encrypted = _encrypt(content, password)
                        files["settings.json.enc"] = {"content": encrypted}
                        print("[Sync] Settings encrypted for push.")
                    else:
                        # No password — push unencrypted (legacy behavior)
                        # Still strip secrets that GitHub would revoke
                        try:
                            settings_data = json.loads(content)
                            for key in SECRET_KEYS:
                                settings_data.pop(key, None)
                            content = json.dumps(settings_data)
                        except (json.JSONDecodeError, ValueError):
                            pass
                        files[filename] = {"content": content}
                        _last_sync_error = ""
                else:
                    files[filename] = {"content": content}
            except IOError:
                continue

    if not files:
        print("[Sync] No data files to push.")
        return False

    print(f"[Sync] Pushing {len(files)} file(s) to Gist...")
    result = _gist_api("PATCH", gist_id, token, {"files": files})
    if result:
        print("[Sync] Push successful.")
        return True
    else:
        print("[Sync] Push failed.")
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
