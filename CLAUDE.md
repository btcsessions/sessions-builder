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

macOS has Python 3.9 (system). CachyOS has a newer version. Both work, but Google API libraries show deprecation warnings on 3.9.
