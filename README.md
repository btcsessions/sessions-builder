# Sessions Builder

A local YouTube production planner: research topics using catalog and inspiration-channel performance, generate and edit recording outlines, manage sponsor/reference links, compare title scores, and export plans as Markdown.

The app uses Flask, JSON files in `data/`, and a browser editor. macOS users can launch it from the Dock with the bundle built by `build_mac_app.sh`. See `CLAUDE.md` for architecture and development conventions.

## Updating an existing installation

Use **Update App** in the running planner. It uses that installation's current Git branch; keep development on `claude/youtube-planning-app-B52L2`. The app restarts automatically and the browser restores its local draft. Saved plans, settings, API keys, and YouTube credentials stay in place. The safety update adds no dependencies and remains compatible with the older Dock launcher's `/planner` readiness check.

Subsequent updates validate startup and installed dependencies in a temporary checkout before applying a fast-forward. Local code changes or missing dependencies stop the update with a message. The button never resets or stashes your edits. An open OAuth authorization flow should be restarted after upgrading from the old fixed cookie secret.

## Data and backups

- JSON saves are atomic, owner-only, and retain a previous version in `.bak` files.
- Password-protected Gist backups encrypt all data files, including OAuth credentials. Without a password, OAuth tokens are excluded and API keys are removed from settings backups.
- Startup never restores remote data over local work. **Restore Backup** is an explicit action that validates all files and retains a recovery snapshot in `data/recovery/`.
- Old backup formats remain readable. Encrypting future backups and deleting current plaintext Gist filenames does not remove older Gist revisions. Previously exposed OAuth credentials may need invalidation/reconnection separately.
- ZIP exports are full migration archives containing unencrypted credentials. Keep them private.
- The active browser draft is separate from saved plan history and cloud backup. Use **Save Plan** for durable plan history.

The default server is `http://127.0.0.1:5000`. Debug mode is off. Network access requires setting both `PLANNER_HOST=0.0.0.0` and `PLANNER_ACCESS_TOKEN` on the host, then signing in from the other device. Use a trusted private network or HTTPS; optional DNS names must be configured in `PLANNER_ALLOWED_HOSTS`.

## Validation

With the app's virtual environment active:

```sh
python -m unittest discover -s tests -v
```

The regression suite uses disposable synthetic data and local Git repositories. It covers backup confidentiality, restore rollback, atomic persistence, concurrent plan saves, OAuth state, browser request protection, incomplete AI output, launcher migration, and safe updates.

`PLANNER_DATA_DIR` selects an isolated data directory. `PLANNER_SKIP_BACKGROUND=1` disables startup network jobs for smoke tests. Do not point tests at your live `data/` directory.

## CachyOS / Linux setup (typed planning, no voice)

Use this same branch on both Mac and Linux. Linux automatically hides voice controls and skips the transcription server; Mac voice behavior is retained. Do not maintain a separate Linux fork.

From the project folder, run `bash setup_linux.sh` once. It creates a private Python environment and a **Sessions Builder** application-menu entry. Open that entry and pin it to your KDE panel. It opens the default browser without a terminal window. Python and Git must already be installed. No Homebrew, Whisper or speech models are needed.

The launcher checks GitHub for validated updates whenever opened. Offline or rejected updates leave the installed version available. For a running app, save your work and use **Update App**. A Mac push becomes available to Linux at its next check; it does not interrupt an active session. Both installations must use this branch, and the Linux support commit must first be published to it. On the Mac, pull shared changes before committing/pushing further work. Future dependency changes may require rerunning setup_linux.sh. Future Mac-only features must retain platform guards.

Open Settings to configure your AI provider and YouTube account. Existing Mac data can be transferred with the app's export/import tools; save the active draft first. Imports replace local documents and should be done on the intended destination. Code updates do not synchronize plans or accounts between machines; do not use both clients as concurrent writers to one backup Gist. Start with independent local data unless a deliberate shared-data workflow is configured.

Logs: `~/.local/state/sessions-builder/` (or `$XDG_STATE_HOME/sessions-builder`). An occupied port is reported without killing another app. Exit through the app's shutdown control. Closing only the browser tab leaves the local planner running; opening the launcher again reopens it.

To remove the menu entry, delete `~/.local/share/applications/sessions-builder.desktop`. Keep the project data until backed up. The installation does not enable a boot service.
