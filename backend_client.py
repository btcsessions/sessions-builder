"""Client for the Sovereign Sessions backend on the Umbrel.

Async-job plumbing only — interactive AI stays local in llm.py. The
backend owns durable state and the job queue; Hermes polls that queue
from its own container, so this client never talks to Hermes directly.

Settings keys (local-only, never synced to the Gist):
  backend_url    e.g. http://100.x.y.z:8799  (Umbrel Tailscale address)
  backend_token  bearer token shared with the backend's .env
"""
from __future__ import annotations

import json
import socket
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


class BackendError(Exception):
    """Backend unreachable or request rejected — message is user-facing."""


def is_configured(settings: dict) -> bool:
    return bool((settings.get("backend_url") or "").strip()
                and (settings.get("backend_token") or "").strip())


def _request(settings: dict, method: str, path: str, body: dict = None,
             timeout: int = 10) -> dict:
    url = (settings.get("backend_url") or "").strip().rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {(settings.get('backend_token') or '').strip()}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as e:
        if e.code in (401, 403):
            raise BackendError("Backend rejected the token — check the bearer token in Settings.")
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:
            pass
        raise BackendError(f"Backend error {e.code}{': ' + detail if detail else ''}")
    except (URLError, socket.timeout, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise BackendError(f"Backend unreachable at {url}: {reason}")
    except json.JSONDecodeError:
        raise BackendError("Backend returned a non-JSON response — is the URL pointing at the right service?")


def health(settings: dict) -> dict:
    return _request(settings, "GET", "/health")


def submit_job(settings: dict, payload: dict) -> dict:
    return _request(settings, "POST", "/jobs", body=payload)


def get_job(settings: dict, job_id: str) -> dict:
    return _request(settings, "GET", f"/jobs/{job_id}")
