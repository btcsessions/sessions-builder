# Sessions Builder - Developer Notes

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

- `sync.py` strips `sync_github_token` and `sync_gist_id` from `settings.json` before pushing to the Gist — this prevents GitHub secret scanning from revoking the token
- On pull, local sync credentials are preserved (they're machine-specific)
- If sync shows 401 errors, the token was likely revoked and needs to be regenerated as a **classic** personal access token with `gist` scope (fine-grained tokens don't support Gists)

### Python Version

macOS has Python 3.9 (system). CachyOS has a newer version. Both work, but Google API libraries show deprecation warnings on 3.9.
