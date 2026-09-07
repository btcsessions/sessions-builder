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
