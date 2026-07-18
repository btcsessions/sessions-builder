# Sessions Builder - Developer Notes

## IMPORTANT: Branch

**Always develop on branch `claude/youtube-planning-app-B52L2`.** Do NOT create new branches. If the session was assigned a different branch, ignore it and use this one. Checkout this branch before making any changes.

## Machine Setup

The app runs on a **single Mac laptop** (Apple Silicon) — uses the `BTC Sessions Planner.app` bundle built with `build_mac_app.sh`.

- **The .app launcher polls `/planner` as its readiness check** (plain curl, requires a literal 200) before opening the browser. The `/planner` shim route in `app.py` must always return 200 directly — removing it (or turning it into a redirect) silently breaks the dock launcher. This bit us once: removing the old /planner page broke app launch.

- **CachyOS (Arch Linux) support is parked**, not removed: Ben retired that machine as an active planner client "for now." The Linux launcher files stay in the repo for potential future use.
- **The recording machine is a delivery target, not a planner install**: finalized plans reach it as exported Markdown outlines (Export button in plan history → `plan_export.py`), never via a second copy of the app.

### Branch Policy

**All development happens on one branch: `claude/youtube-planning-app-B52L2`.**

Never create separate branches per machine or per platform. The "Update App" button in the GUI pulls from the current branch automatically.

### Platform-Specific Files

- `build_mac_app.sh` — macOS only (builds .app bundle with native Swift launcher)
- `yt-planner.desktop`, `launch.fish` — parked Linux support (kept for potential future use)
- `setup.sh`, `launch.sh`, `app.py`, `sync.py` — shared, cross-platform

### Gist Sync

Gist sync now serves as an **encrypted off-machine backup**. It was built for multi-machine sync and would resume that role if a second machine returns; the mechanics are unchanged. Important:

- **Encryption**: `settings.json` is encrypted with a user-set sync password before pushing to the Gist. This prevents GitHub secret scanning from detecting and revoking API keys (confirmed: both GitHub tokens and Anthropic keys were revoked this way). The password is set once per machine in Settings and stored locally only.
- **Local-only keys**: `sync_github_token`, `sync_gist_id`, and `sync_password` are stripped before push and preserved from local settings on pull — they never appear in the Gist.
- **Fallback**: If no sync password is set, settings.json is pushed unencrypted but with API keys stripped (legacy behavior).
- If sync shows 401 errors, the token was likely revoked — regenerate as a **classic** personal access token with `gist` scope (fine-grained tokens don't support Gists)
- The `cryptography` package is required for encryption (in requirements.txt)

### Python Version

macOS has Python 3.9 (system). Google API libraries show deprecation warnings on 3.9. Keep code 3.9-compatible (use `from __future__ import annotations` for `X | Y` type hints).

## Hermes/Umbrel Integration (planned)

Agreed direction for connecting the planner to Ben's Hermes agent on his Umbrel — for context when this work starts:

- A **separate backend container on the Umbrel** owns durable state (SQLite documents store), a job queue, and a token-auth HTTP API reached over Tailscale. No sidecar inside the Hermes app container (its packaging/proxy doesn't support one).
- **Hermes-pull job loop**: the app never talks to Hermes directly. The app submits async jobs to the backend; Hermes polls the backend's queue on cron from inside its container, reasons, and posts results back.
- **Interactive AI stays local** via `llm.py` (with the LM Studio offline fallback) permanently — backend jobs are for async work only (recording handoff, post-mortems, ingestion).
- `plan_export.py` renders the recording-outline document as a pure function of the plan dict so the future backend handoff job reuses it unchanged.
- **Preference boundary**: Hermes owns durable cross-workflow facts about Ben (style, workflow rules); the laptop owns project/domain data (plans, catalog, analytics). Each fact has exactly one home; the planner may *propose* preferences upward for Hermes review, never write them.

## Sovereign Sessions Tutorial Planner

The planner is branded for the **Sovereign Sessions** channel (freedom tech: bitcoin, privacy, self-hosted AI, sovereign computing). Key facts:

- **Channel Name** and **Channel Niche** are Settings fields (defaults: "Sovereign Sessions" and the freedom-tech niche in `brainstorm.py`). The name drives the nav brand and page titles; the niche is injected into every AI prompt.
- **Two YouTube data sources** inform outputs: the YouTube Data API key (playlist/video stats) and the OAuth-connected YouTube Analytics API (retention, CTR, impressions).
- **Own-channel roles** (Inspiration page, `own_role` on competitor entries): `current` = the new channel plans are FOR (top-priority signal, weight grows with catalog size); `legacy` = the previous main channel (strong but fading reference); `""` = regular competitor. Old `is_own_channel: true` entries are treated as `legacy`. When the new channel has real content, flip primary: swap the playlist URL in Settings and reconnect OAuth with the new channel's Google account (add it as a test user on the OAuth consent screen if the Cloud project is in testing mode).
- **AI providers** (`llm.py`): Anthropic (default), OpenAI, Maple AI (via local Maple Proxy), and LM Studio — the last three share one OpenAI-compatible urllib client. Active provider is a Settings dropdown (`ai_provider`); only providers with credentials are selectable. Network errors auto-fall back to LM Studio so the app works offline. Anthropic-only feature: web search during plan generation (silently skipped elsewhere). `openai_key`/`maple_key` are in sync's SECRET_KEYS.
- **Format inference**: the workspace has no format picker. Blank `video_type` makes the AI infer the format (returned in the plan JSON as `video_type`, shown as a badge). Endpoints still accept an explicit `video_type` for saved plans.
- **Plan generation** (`generate_video_plan`): each outline section includes a `section_script` (short scripted on-camera opener for compartmentalized learning). Descriptions are SEO-optimized and pull REAL URLs from past tutorials and the **link library** (`affiliate_links` in settings — one list for both affiliates and sponsors via a `type` field, plus optional `blurb`). Affiliates are AI-woven into descriptions when relevant (near the top); **sponsors are never sent to the AI** — the app deterministically appends selected sponsors' blurbs verbatim after the description body (before the timestamps placeholder). Sponsors are chosen per episode via checkboxes in the workspace form (`sponsors` in the generate/parse payloads); the legacy `sponsor_template` setting remains as a fallback when no sponsors are selected and has no Settings UI anymore.
- **Web search**: plan generation uses the Anthropic `web_search_20260209` server tool (max 3 searches) and silently falls back to no-search if unavailable.
- **Anthropic model**: a Settings field (`anthropic_model`, blank = `llm.DEFAULT_ANTHROPIC_MODEL`, currently `claude-opus-4-8`). Hardcoded date-suffixed model IDs get retired and start 404ing (this happened with `claude-sonnet-4-20250514`) — keep the default an alias and configurable.
- **YouTube trending** feeds trend intel (`trend_intel.py`): overall + Science & Tech mostPopular charts, fetched with the Google API key from settings.
- **No trends GUI**: the `/trends` page was removed (along with the analyze/remix/"Plan This Video" flow, thumbnail-vision market analysis, and AI trend report). Trend awareness is internal-only: `trends.py` (`analyze_trends`), `trend_intel.py`, and `market.py` still feed chat, plan generation, and notes parsing as prompt context. Startup still records view-count snapshots (`record_snapshot`) for future velocity/longevity use.
- `save_settings()` in `app.py` must preserve keys it doesn't manage (affiliate_links, sponsor_template, default_yt_tags, ...) — it starts from the existing dict and updates. Don't rebuild the settings dict from scratch.
- **Mobile access**: set `PLANNER_HOST=0.0.0.0` (and optionally `PLANNER_PORT`) before launch to reach the app from a phone on the same network at `http://<machine-ip>:5000`. Default stays `127.0.0.1`.
- **Voice dictation** (Wispr Flow style): mic buttons on Import-Notes, Amend-Notes, and the Brainstorm chat input (`static/dictation.js`, manual click-to-start/click-to-stop toggle — no silence detection). Audio goes to `/api/transcribe` → `llm.transcribe_audio()` → a **local** OpenAI-compatible Whisper server (`whisper_url` setting, default `http://127.0.0.1:8090/v1`, keyless like LM Studio; installed once via `bash setup_whisper.sh`, which builds whisper.cpp's `whisper-server` from source — the Homebrew whisper-cpp formula ships WITHOUT the server binary — and downloads `ggml-large-v3-turbo.bin` into `data/whisper/`; `--convert` + ffmpeg let it accept the browser's webm/opus directly). `whisper_manager.py` auto-starts/stops the server with the planner (only when the URL is local, the port is free, and binary+model exist — a user-run server always wins; stops via atexit + SIGTERM handler since the .app wrapper and /api/shutdown both use SIGTERM). The raw transcript is then cleaned by `brainstorm.refine_dictation()` (strips filler/rambling, organizes; never adds content or answers) via a **dedicated dictation-cleanup override**: `dictation_provider`/`dictation_model` settings, blank = active provider with a fast Anthropic default (`llm.DEFAULT_DICTATION_ANTHROPIC_MODEL`, alias not dated id). Point it at LM Studio to keep the whole voice path local. A refine failure still returns the raw transcript. Mic requires a secure context — works on `127.0.0.1`, disabled (with hint) on the `PLANNER_HOST=0.0.0.0` phone path.
