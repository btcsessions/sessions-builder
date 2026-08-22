"""vidIQ MCP client — lets the planner call vidIQ tools directly.

Speaks MCP (JSON-RPC 2.0 over Streamable HTTP) to vidIQ's MCP server with
a user-supplied connection key, so the app can score titles and research
keywords itself — no Claude client in the loop. stdlib urllib only, like
the other integrations.

Settings keys (Settings page, "vidIQ Integration"):
  - vidiq_mcp_key      — the MCP connection key (Bearer token). Secret.
  - vidiq_mcp_url      — MCP endpoint URL (blank = DEFAULT_VIDIQ_MCP_URL).
  - vidiq_channel_id   — optional YouTube channel ID; improves score accuracy.

Every helper is best-effort friendly: callers wrap in try/except so a vidIQ
outage never blocks plan generation. Tool calls cost vidIQ credits
(~5/call), so the optimization pass is deliberately bounded.
"""
from __future__ import annotations

import json
import threading
from urllib.request import urlopen, Request
from urllib.error import HTTPError

DEFAULT_VIDIQ_MCP_URL = "https://mcp.vidiq.com/mcp"
PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 90

# One cached MCP session per (url, key); re-initialized on session errors
_session_lock = threading.Lock()
_session_id = None
_session_for = None  # (url, key) the cached session belongs to
_rpc_id = 0


def is_configured(settings: dict) -> bool:
    return bool((settings.get("vidiq_mcp_key") or "").strip())


def _endpoint(settings: dict) -> str:
    return (settings.get("vidiq_mcp_url") or "").strip() or DEFAULT_VIDIQ_MCP_URL


def _headers(settings: dict, session_id: str = None) -> dict:
    key = (settings.get("vidiq_mcp_key") or "").strip()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {key}",
        "x-api-key": key,
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _parse_body(raw: str, content_type: str, want_id) -> dict:
    """Extract the JSON-RPC response, from plain JSON or an SSE stream."""
    if "text/event-stream" in content_type:
        best = None
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                msg = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                best = msg
                if msg.get("id") == want_id:
                    return msg
        if best is not None:
            return best
        raise RuntimeError("vidIQ MCP: no JSON-RPC response in event stream")
    return json.loads(raw) if raw.strip() else {}


def _post(settings: dict, payload: dict, session_id: str = None):
    """POST one JSON-RPC message; returns (parsed response, session header)."""
    url = _endpoint(settings)
    req = Request(url, data=json.dumps(payload).encode("utf-8"),
                  headers=_headers(settings, session_id), method="POST")
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            content_type = resp.headers.get("Content-Type", "")
            new_session = resp.headers.get("Mcp-Session-Id") or session_id
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("vidIQ MCP key rejected (401/403) — check the "
                               "connection key in Settings")
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise _SessionError(e.code, f"vidIQ MCP error {e.code}: {detail or e.reason}")
    if "id" not in payload:  # notification — no response expected
        return None, new_session
    body = _parse_body(raw, content_type, payload["id"])
    if body.get("error"):
        err = body["error"]
        raise RuntimeError(f"vidIQ MCP: {err.get('message', err)}")
    return body.get("result", {}), new_session


class _SessionError(RuntimeError):
    """HTTP-level failure that may mean the MCP session expired."""
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _next_id() -> int:
    global _rpc_id
    _rpc_id += 1
    return _rpc_id


def _initialize(settings: dict) -> str:
    result, session = _post(settings, {
        "jsonrpc": "2.0", "id": _next_id(), "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "sessions-builder", "version": "1.0"},
        },
    })
    _post(settings, {"jsonrpc": "2.0", "method": "notifications/initialized"},
          session_id=session)
    return session


def _ensure_session(settings: dict, force: bool = False) -> str:
    global _session_id, _session_for
    ident = (_endpoint(settings), (settings.get("vidiq_mcp_key") or "").strip())
    with _session_lock:
        if force or _session_id is None or _session_for != ident:
            _session_id = _initialize(settings)
            _session_for = ident
        return _session_id


def call_tool(settings: dict, name: str, arguments: dict) -> dict:
    """Call one vidIQ MCP tool and return its parsed payload."""
    if not is_configured(settings):
        raise RuntimeError("vidIQ MCP is not configured (set the key in Settings)")
    session = _ensure_session(settings)
    payload = {"jsonrpc": "2.0", "id": _next_id(), "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    try:
        result, _ = _post(settings, payload, session_id=session)
    except _SessionError as e:
        if e.code not in (400, 404):
            raise
        # Session likely expired — re-initialize once and retry
        session = _ensure_session(settings, force=True)
        payload["id"] = _next_id()
        result, _ = _post(settings, payload, session_id=session)
    if result.get("isError"):
        raise RuntimeError(f"vidIQ tool {name} failed: {_result_text(result)[:300]}")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    text = _result_text(result)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"data": parsed}
    except json.JSONDecodeError:
        return {"text": text}


def _result_text(result: dict) -> str:
    return "\n".join(
        c.get("text", "") for c in result.get("content", [])
        if isinstance(c, dict) and c.get("type") == "text"
    ).strip()


def test_connection(settings: dict) -> dict:
    """Cheapest end-to-end check (vidiq_user_channels costs 0 credits)."""
    return call_tool(settings, "vidiq_user_channels", {})


def _find_score(obj):
    """Fish a 0-100 score out of a defensively-unknown payload shape."""
    if isinstance(obj, (int, float)):
        return round(float(obj))
    if isinstance(obj, dict):
        for key in ("score", "titleScore", "overall", "value"):
            if isinstance(obj.get(key), (int, float)):
                return round(float(obj[key]))
        for v in obj.values():
            found = _find_score(v) if isinstance(v, dict) else None
            if found is not None:
                return found
    return None


def score_title(settings: dict, title: str, video_type: str = "long"):
    """vidIQ CTR score (0-100) for one title, or None if unparseable."""
    args = {"title": title, "type": video_type}
    channel_id = (settings.get("vidiq_channel_id") or "").strip()
    if channel_id:
        args["channelId"] = channel_id
    return _find_score(call_tool(settings, "vidiq_score_title", args))


def generate_titles(settings: dict, title: str = "", description: str = "",
                    num: int = 5, previous_titles: list = None) -> list:
    """Scored title suggestions from vidIQ: [{"title": str, "score": int|None}]."""
    args = {"numTitles": max(1, min(10, num)), "type": "long"}
    if title:
        args["title"] = title[:500]
    if description:
        args["description"] = description[:5000]
    if previous_titles:
        args["previousTitles"] = [t[:200] for t in previous_titles if t][:20]
    payload = call_tool(settings, "vidiq_generate_titles", args)
    out = []
    for item in _find_title_list(payload):
        if isinstance(item, str):
            out.append({"title": item, "score": None})
        elif isinstance(item, dict):
            text = item.get("title") or item.get("text") or ""
            if text:
                out.append({"title": text, "score": _find_score(item)})
    return out


def _find_title_list(payload) -> list:
    """Locate the suggestions list in an unknown payload shape."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("titles", "suggestions", "results", "data", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
        for v in payload.values():
            if isinstance(v, list) and v and isinstance(v[0], (dict, str)):
                return v
    return []


def optimize_titles(settings: dict, titles: list, description: str = "",
                    topic: str = "", max_titles: int = 6) -> list:
    """Score the plan's titles, add vidIQ's own scored suggestions, and
    return the combined list sorted best-first:
    [{"title", "vidiq_score", "source": "plan"|"vidiq"}]

    Bounded cost: up to 4 score calls + 1 generate call (~25 credits).
    """
    scored = []
    for t in [t for t in titles if (t or "").strip()][:4]:
        try:
            scored.append({"title": t, "vidiq_score": score_title(settings, t),
                           "source": "plan"})
        except Exception as e:
            print(f"[vidIQ] score failed for '{t[:50]}': {e}")
            scored.append({"title": t, "vidiq_score": None, "source": "plan"})
    try:
        seed = titles[0] if titles else topic
        for s in generate_titles(settings, title=seed, description=description,
                                 num=5, previous_titles=titles):
            scored.append({"title": s["title"], "vidiq_score": s["score"],
                           "source": "vidiq"})
    except Exception as e:
        print(f"[vidIQ] title suggestions failed: {e}")

    seen = set()
    unique = []
    for s in scored:
        key = s["title"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(s)
    # Best first; unscored plan titles stay above unscored extras
    unique.sort(key=lambda s: (s["vidiq_score"] is not None, s["vidiq_score"] or 0),
                reverse=True)
    return unique[:max_titles]


def keyword_prompt_section(settings: dict, topic: str, max_keywords: int = 12):
    """Real search metrics for the topic, as (prompt_section, top_keywords).

    top_keywords are the highest-opportunity related keyword strings, for
    merging into the plan's YouTube tags.
    """
    if not (topic or "").strip():
        return "", []
    payload = call_tool(settings, "vidiq_keyword_research",
                        {"keyword": topic.strip()[:100], "mode": "research"})
    lines = []
    seed = payload.get("keyword") or payload.get("seed") or {}
    if isinstance(seed, dict) and seed:
        lines.append("Seed keyword \"%s\": %s" % (
            seed.get("keyword", topic), _metrics_line(seed)))
    related = payload.get("relatedKeywords") or payload.get("related") or []
    rows = []
    for r in related:
        if not isinstance(r, dict) or not r.get("keyword"):
            continue
        rows.append(r)
    rows.sort(key=lambda r: r.get("overall") or r.get("score") or 0, reverse=True)
    rows = rows[:max_keywords]
    for r in rows:
        lines.append(f"- {r['keyword']}: {_metrics_line(r)}")
    if not lines:
        return "", []
    section = (
        "\n**VIDIQ KEYWORD DATA (real YouTube search metrics for this topic):**\n"
        + "\n".join(lines) +
        "\nWork the highest-opportunity keywords naturally into the titles, the "
        "first sentences of the description, and the tags. Prefer high volume + "
        "low competition."
    )
    top_keywords = [r["keyword"] for r in rows if (r.get("overall") or 0) > 0]
    return section, top_keywords


def _metrics_line(row: dict) -> str:
    parts = []
    for label, keys in (("volume", ("volume",)), ("competition", ("competition",)),
                        ("opportunity", ("overall", "score"))):
        for k in keys:
            if isinstance(row.get(k), (int, float)):
                parts.append(f"{label} {round(row[k])}/100")
                break
    monthly = row.get("estimatedMonthlySearchVolume") or row.get("monthlyVolume")
    if isinstance(monthly, (int, float)):
        parts.append(f"~{int(monthly):,} searches/mo")
    return ", ".join(parts) if parts else "no metrics"
