# Sessions Builder - Developer Notes

## IMPORTANT: Branch

**Always develop on branch `claude/youtube-planning-app-B52L2`.** Do NOT create new branches. If the session was assigned a different branch, ignore it and use this one. Checkout this branch before making any changes.

## Multi-Machine Setup

This app runs on **multiple machines** (same user, same repo, same branch):

- **macOS** (Apple Silicon Macs) — uses `BTC Sessions Planner.app` bundle built with `build_mac_app.sh`
- **CachyOS (Arch Linux)** — uses `yt-planner.desktop` launcher and `launch.sh`

### Branch Policy

**All development happens on one branch: `claude/youtube-planning-app-B52L2`.**

Never create separate branches per machine or per platform. Both machines pull from and push to this single branch. The "Update App" button in the GUI pulls from the current branch automatically.

### Platform-Specific Files

- `build_mac_app.sh` — macOS only (builds .app bundle with native Swift launcher)
- `yt-planner.desktop` — Linux only (desktop launcher)
- `setup.sh`, `launch.sh`, `app.py`, `sync.py` — shared, cross-platform

### Gist Sync

The app syncs data between machines via a private GitHub Gist. Important:

- **Encryption**: `settings.json` is encrypted with a user-set sync password before pushing to the Gist. This prevents GitHub secret scanning from detecting and revoking API keys (confirmed: both GitHub tokens and Anthropic keys were revoked this way). The password is set once per machine in Settings and stored locally only.
- **Local-only keys**: `sync_github_token`, `sync_gist_id`, and `sync_password` are stripped before push and preserved from local settings on pull — they never appear in the Gist.
- **Fallback**: If no sync password is set, settings.json is pushed unencrypted but with API keys stripped (legacy behavior).
- If sync shows 401 errors, the token was likely revoked — regenerate as a **classic** personal access token with `gist` scope (fine-grained tokens don't support Gists)
- The `cryptography` package is required for encryption (in requirements.txt)

### Python Version

macOS has Python 3.9 (system). CachyOS has a newer version. Both work, but Google API libraries show deprecation warnings on 3.9. Keep code 3.9-compatible (use `from __future__ import annotations` for `X | Y` type hints).

## Sovereign Sessions Tutorial Planner

The planner is branded for the **Sovereign Sessions** channel (freedom tech: bitcoin, privacy, self-hosted AI, sovereign computing). Key facts:

- **Channel Name** and **Channel Niche** are Settings fields (defaults: "Sovereign Sessions" and the freedom-tech niche in `brainstorm.py`). The name drives the nav brand and page titles; the niche is injected into every AI prompt.
- **Two YouTube data sources** inform outputs: the YouTube Data API key (playlist/video stats) and the OAuth-connected YouTube Analytics API (retention, CTR, impressions).
- **Own-channel roles** (Inspiration page, `own_role` on competitor entries): `current` = the new channel plans are FOR (top-priority signal, weight grows with catalog size); `legacy` = the previous main channel (strong but fading reference); `""` = regular competitor. Old `is_own_channel: true` entries are treated as `legacy`. When the new channel has real content, flip primary: swap the playlist URL in Settings and reconnect OAuth with the new channel's Google account (add it as a test user on the OAuth consent screen if the Cloud project is in testing mode).
- **AI providers** (`llm.py`): Anthropic (default), OpenAI, Maple AI (via local Maple Proxy), and LM Studio — the last three share one OpenAI-compatible urllib client. Active provider is a Settings dropdown (`ai_provider`); only providers with credentials are selectable. Network errors auto-fall back to LM Studio so the app works offline. Anthropic-only features: web search during plan generation (silently skipped elsewhere) and thumbnail-vision market analysis (returns an explicit error without an Anthropic key). `openai_key`/`maple_key` are in sync's SECRET_KEYS.
- **Format inference**: the workspace has no format picker. Blank `video_type` makes the AI infer the format (returned in the plan JSON as `video_type`, shown as a badge). Endpoints still accept an explicit `video_type` for saved plans.
- **Plan generation** (`generate_video_plan`): each outline section includes a `section_script` (short scripted on-camera opener for compartmentalized learning). Descriptions are SEO-optimized and pull REAL URLs from past tutorials and the referral/affiliate **link library** (`affiliate_links` in settings, managed on the plan page).
- **Web search**: plan generation uses the Anthropic `web_search_20250305` server tool (max 3 searches) and silently falls back to no-search if unavailable.
- **YouTube trending** feeds trend intel (`trend_intel.py`): overall + Science & Tech mostPopular charts, fetched with the Google API key from settings.
- `save_settings()` in `app.py` must preserve keys it doesn't manage (affiliate_links, sponsor_template, default_yt_tags, ...) — it starts from the existing dict and updates. Don't rebuild the settings dict from scratch.
- **Mobile access**: set `PLANNER_HOST=0.0.0.0` (and optionally `PLANNER_PORT`) before launch to reach the app from a phone on the same network at `http://<machine-ip>:5000`. Default stays `127.0.0.1`.
