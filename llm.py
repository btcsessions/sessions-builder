"""Multi-provider AI layer.

Routes chat completions to the active provider chosen in Settings:

  - anthropic  — Anthropic SDK (the only provider with web search support)
  - openai     — OpenAI Chat Completions API
  - maple      — Maple AI via Maple Proxy (OpenAI-compatible, runs locally)
  - lmstudio   — LM Studio local server (OpenAI-compatible, no key needed)

OpenAI, Maple, and LM Studio all speak the OpenAI chat-completions wire
format, so one urllib-based client covers all three (no new dependencies).

Offline fallback: if a remote provider fails with a network error and an
LM Studio URL is configured, the call is retried against LM Studio so the
app keeps working without an internet connection.
"""
from __future__ import annotations

import json
import socket
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

PROVIDERS = ["anthropic", "openai", "maple", "lmstudio"]

PROVIDER_LABELS = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI",
    "maple": "Maple AI",
    "lmstudio": "LM Studio (local)",
}

DEFAULT_MAPLE_URL = "http://localhost:8080/v1"
DEFAULT_LMSTUDIO_URL = "http://localhost:1234/v1"
DEFAULT_WHISPER_URL = "http://127.0.0.1:8090/v1"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
# Dictation cleanup is an easy, latency-sensitive task — default to a fast
# model, not the heavyweight plan-generation model. Alias, not a dated id
# (dated ids get retired and 404).
DEFAULT_DICTATION_ANTHROPIC_MODEL = "claude-haiku-4-5"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_MAPLE_MODEL = "llama3-3-70b"  # Maple Proxy REQUIRES a model; GET /v1/models lists options
DEFAULT_LMSTUDIO_MODEL = ""   # LM Studio uses whatever model is loaded

# What actually served the last call — for surfacing in the UI
_last_provider_used = ""


def get_last_provider_used() -> str:
    return _last_provider_used


def is_configured(settings: dict, provider: str) -> bool:
    """A provider is configured when its credential (or URL) is set."""
    if provider == "anthropic":
        import os
        return bool(settings.get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "openai":
        return bool(settings.get("openai_key"))
    if provider == "maple":
        return bool(settings.get("maple_key") or settings.get("maple_url"))
    if provider == "lmstudio":
        # Always available to select — it has a sensible default URL
        return True
    return False


def configured_providers(settings: dict) -> list:
    return [p for p in PROVIDERS if is_configured(settings, p)]


def any_configured(settings: dict) -> bool:
    """True if at least one provider that can actually serve is set up.

    LM Studio counts only when explicitly chosen or given a URL, so a
    totally blank install still prompts for a key.
    """
    if settings.get("anthropic_key") or settings.get("openai_key") or settings.get("maple_key"):
        return True
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if settings.get("lmstudio_url") or settings.get("ai_provider") == "lmstudio":
        return True
    return False


def active_provider(settings: dict) -> str:
    """The provider to use: the configured choice, else first configured."""
    choice = (settings.get("ai_provider") or "").strip()
    if choice in PROVIDERS and is_configured(settings, choice):
        return choice
    for p in PROVIDERS:
        if p == "lmstudio":
            continue  # never auto-pick lmstudio over a configured cloud key
        if is_configured(settings, p):
            return p
    return "lmstudio"


def _anthropic_chat(settings: dict, system: str, messages: list, max_tokens: int,
                    use_web_search: bool = False, model: str = None) -> str:
    import os
    import anthropic
    api_key = settings.get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=api_key)
    model = model or (settings.get("anthropic_model") or "").strip() or DEFAULT_ANTHROPIC_MODEL
    kwargs = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if use_web_search:
        try:
            response = client.messages.create(
                **kwargs,
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
            )
            return _anthropic_text(response)
        except Exception as e:
            print(f"[LLM] Anthropic web search unavailable, retrying without: {e}")
    response = client.messages.create(**kwargs)
    return _anthropic_text(response)


def _anthropic_text(response) -> str:
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()


def _openai_compat_chat(base_url: str, api_key: str, model: str, system: str,
                        messages: list, max_tokens: int, timeout: int = 180) -> str:
    """Call an OpenAI-compatible /chat/completions endpoint via urllib."""
    url = base_url.rstrip("/") + "/chat/completions"
    oai_messages = []
    if system:
        oai_messages.append({"role": "system", "content": system})
    for m in messages:
        content = m.get("content", "")
        # Flatten Anthropic-style content blocks to plain text
        if isinstance(content, list):
            content = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        oai_messages.append({"role": m.get("role", "user"), "content": content})

    # OpenAI's newer models reject max_tokens in favor of max_completion_tokens;
    # local OpenAI-compatible servers (Maple, LM Studio) still expect max_tokens.
    if "api.openai.com" in base_url:
        body = {"messages": oai_messages, "max_completion_tokens": max_tokens}
    else:
        body = {"messages": oai_messages, "max_tokens": max_tokens}
    if model:
        body["model"] = model

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(_http_error_message(url, e))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Empty response from {url}")
    return (choices[0].get("message", {}).get("content") or "").strip()


def _http_error_message(url: str, e: HTTPError) -> str:
    """Surface the response body — it says WHY (wrong model, no model
    loaded, bad key) instead of a bare "HTTP Error 400"."""
    detail = ""
    try:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("error", {}).get("message", "") or raw
        except (json.JSONDecodeError, AttributeError):
            detail = raw
    except Exception:
        pass
    detail = (detail or "").strip()[:300]
    return f"{url} error {e.code}{': ' + detail if detail else ''}"


def transcribe_audio(settings: dict, audio: bytes, filename: str = "audio.webm",
                     content_type: str = "audio/webm") -> str:
    """Transcribe audio via a local OpenAI-compatible Whisper server.

    POSTs multipart/form-data to <whisper_url>/audio/transcriptions —
    keyless like LM Studio (Authorization header only if whisper_key is
    set). The multipart body is built by hand so we stay on urllib.
    """
    import uuid
    base = (settings.get("whisper_url") or DEFAULT_WHISPER_URL).rstrip("/")
    url = base + "/audio/transcriptions"
    model = (settings.get("whisper_model") or "").strip() or "whisper-1"

    boundary = "----planner-" + uuid.uuid4().hex
    parts = []
    for name, value in (("model", model), ("response_format", "text")):
        parts.append(
            "--{b}\r\nContent-Disposition: form-data; name=\"{n}\"\r\n\r\n{v}\r\n"
            .format(b=boundary, n=name, v=value).encode("utf-8"))
    parts.append(
        "--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{f}\"\r\n"
        "Content-Type: {c}\r\n\r\n"
        .format(b=boundary, f=filename.replace('"', ""), c=content_type).encode("utf-8"))
    parts.append(audio)
    parts.append("\r\n--{b}--\r\n".format(b=boundary).encode("utf-8"))
    body = b"".join(parts)

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if settings.get("whisper_key"):
        headers["Authorization"] = f"Bearer {settings['whisper_key']}"

    req = Request(url, data=body, headers=headers, method="POST")
    try:
        # Long timeout: local transcription of a long ramble can be slow.
        with urlopen(req, timeout=600) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        raise RuntimeError(_http_error_message(url, e))
    # response_format=text returns plain text, but some servers reply JSON anyway
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return (json.loads(raw).get("text") or "").strip()
        except json.JSONDecodeError:
            pass
    return raw


def _get_json(url: str, headers: dict, timeout: int = 10) -> dict:
    """GET a JSON endpoint via urllib."""
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(_http_error_message(url, e))


# Non-chat OpenAI models that would clutter the model dropdown.
_OPENAI_NON_CHAT_PREFIXES = ("whisper-", "tts-", "dall-e", "text-embedding", "omni-moderation")


def _openai_compat_list_models(base_url: str, api_key: str) -> list:
    """Model IDs from an OpenAI-compatible GET /models endpoint."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = _get_json(base_url.rstrip("/") + "/models", headers)
    models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    if "api.openai.com" in base_url:
        models = [m for m in models if not m.startswith(_OPENAI_NON_CHAT_PREFIXES)]
    return sorted(models)


def _anthropic_list_models(api_key: str) -> list:
    """Model IDs from the Anthropic models API (already newest-first)."""
    data = _get_json(
        "https://api.anthropic.com/v1/models?limit=100",
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


def list_models(provider: str, settings: dict) -> list:
    """Model IDs available from a provider, for the Settings dropdowns.

    Raises RuntimeError/URLError/socket.timeout on failure — callers turn
    those into a friendly message; typing a model manually always works.
    """
    if provider == "anthropic":
        import os
        api_key = settings.get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("No Anthropic API key set")
        return _anthropic_list_models(api_key)
    if provider == "openai":
        api_key = settings.get("openai_key", "")
        if not api_key:
            raise RuntimeError("No OpenAI API key set")
        return _openai_compat_list_models("https://api.openai.com/v1", api_key)
    if provider == "maple":
        return _openai_compat_list_models(
            settings.get("maple_url") or DEFAULT_MAPLE_URL,
            settings.get("maple_key", ""),
        )
    if provider == "lmstudio":
        return _openai_compat_list_models(
            settings.get("lmstudio_url") or DEFAULT_LMSTUDIO_URL, "")
    raise ValueError(f"Unknown provider: {provider}")


def _provider_call(provider: str, settings: dict, system: str, messages: list,
                   max_tokens: int, use_web_search: bool,
                   anthropic_model: str = None) -> str:
    if provider == "anthropic":
        return _anthropic_chat(settings, system, messages, max_tokens, use_web_search,
                               model=anthropic_model)
    if provider == "openai":
        return _openai_compat_chat(
            "https://api.openai.com/v1",
            settings.get("openai_key", ""),
            settings.get("openai_model") or DEFAULT_OPENAI_MODEL,
            system, messages, max_tokens,
        )
    if provider == "maple":
        return _openai_compat_chat(
            settings.get("maple_url") or DEFAULT_MAPLE_URL,
            settings.get("maple_key", ""),
            settings.get("maple_model") or DEFAULT_MAPLE_MODEL,
            system, messages, max_tokens,
        )
    if provider == "lmstudio":
        return _openai_compat_chat(
            settings.get("lmstudio_url") or DEFAULT_LMSTUDIO_URL,
            "",
            settings.get("lmstudio_model") or DEFAULT_LMSTUDIO_MODEL,
            system, messages, max_tokens,
            timeout=600,  # local models can be slow
        )
    raise ValueError(f"Unknown provider: {provider}")


def _is_network_error(exc: Exception) -> bool:
    """Connectivity failures (no internet / host unreachable), not API errors."""
    if isinstance(exc, HTTPError):
        return False  # got a response — server reachable, request rejected
    if isinstance(exc, (URLError, socket.timeout, ConnectionError, OSError)):
        return True
    # Anthropic SDK wraps connection problems in APIConnectionError
    return "connection" in type(exc).__name__.lower()


def llm_chat(settings: dict, system: str, messages: list, max_tokens: int = 3000,
             use_web_search: bool = False,
             anthropic_model: str = None) -> str:
    """Send a chat request to the active provider.

    Falls back to LM Studio on network errors so the app works offline.
    """
    global _last_provider_used
    provider = active_provider(settings)
    try:
        result = _provider_call(provider, settings, system, messages, max_tokens,
                                use_web_search, anthropic_model)
        _last_provider_used = provider
        return result
    except Exception as e:
        if provider != "lmstudio" and _is_network_error(e):
            print(f"[LLM] {provider} unreachable ({e}) — falling back to LM Studio")
            result = _provider_call("lmstudio", settings, system, messages, max_tokens, False)
            _last_provider_used = "lmstudio (offline fallback)"
            return result
        raise
