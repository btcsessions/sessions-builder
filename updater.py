"""Fast-forward updates with validation before changing the running checkout."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading

UPDATE_LOCK = threading.Lock()


def _git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, text=True,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Git operation failed")
    return result.stdout.strip()


def prepare_update(root):
    """Never reset/stash user edits or change branches. No dependency mutations."""
    root = Path(root)
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Local code changes are present. Save or commit them before updating; nothing was overwritten.")
    branch = _git(root, "branch", "--show-current")
    if not branch:
        raise RuntimeError("This checkout has no active branch")
    before = _git(root, "rev-parse", "HEAD")
    _git(root, "fetch", "origin", branch)
    target = _git(root, "rev-parse", "FETCH_HEAD")
    if target == before:
        return {"updated": False, "message": "Already up to date."}
    local_only, remote_only = map(int, _git(
        root, "rev-list", "--left-right", "--count", before + "..." + target).split())
    if local_only and not remote_only:
        return {"updated": False, "message":
                "This installation has unpublished changes and is ahead of GitHub. "
                "Publish those changes to the shared branch before receiving further updates. Nothing was changed."}
    if local_only:
        raise RuntimeError("This installation and GitHub both have new changes. "
                           "Merge and publish them before updating. Your local code and data were not changed.")
    archive = subprocess.run(["git", "archive", target], cwd=root,
                             capture_output=True, check=True, timeout=30).stdout
    with tempfile.TemporaryDirectory(prefix="planner-update-") as temp:
        candidate = Path(temp) / "code"
        candidate.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar.getmembers():
                if member.issym() or member.islnk() or member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise RuntimeError("Update contains an unsafe archive entry")
            tar.extractall(candidate)
        # A future dependency change must already be satisfied. Never partially
        # modify the working environment and then leave the old code running.
        check = subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--no-index",
                                "-r", str(candidate / "requirements.txt")],
                               capture_output=True, text=True, timeout=60)
        if check.returncode:
            raise RuntimeError("The update needs dependencies that are not installed. Run setup.sh in the app folder, then retry Update App. Your code was not changed.")
        env = dict(os.environ, PLANNER_SKIP_BACKGROUND="1", PLANNER_DATA_DIR=str(Path(temp) / "data"),
                   PYTHONDONTWRITEBYTECODE="1")
        check = subprocess.run([sys.executable, "-c", """
import pathlib
for path in pathlib.Path('.').glob('*.py'):
    compile(path.read_text(), str(path), 'exec')
import app
client = app.app.test_client()
assert client.get('/planner').status_code == 200
assert client.get('/settings').status_code == 200
"""], cwd=candidate, env=env, capture_output=True, text=True, timeout=30)
        if check.returncode:
            raise RuntimeError("The updated app failed its startup check. Your code was not changed.")
    # Recheck after validation in case another process edited the checkout.
    if _git(root, "rev-parse", "HEAD") != before or _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Local code changed during validation. Retry when editing is finished.")
    _git(root, "merge", "--ff-only", target)
    return {"updated": True, "restarting": True, "revision": target,
            "message": "Update validated. Restarting the planner."}


def patch_installed_launcher(root):
    """Migrate only the known unsafe shell snippet; no Swift rebuild needed."""
    from storage import atomic_text
    path = Path(root) / "BTC Sessions Planner.app/Contents/MacOS/server.sh"
    if not path.exists():
        return
    old = '/usr/sbin/lsof -ti:5000 | xargs kill -9 2>/dev/null'
    script = path.read_text()
    if old not in script:
        return
    safe = '''if /usr/sbin/lsof -tiTCP:"${PLANNER_PORT:-5000}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Planner port is already occupied. Close the existing service before relaunching."
    exit 1
fi'''
    atomic_text(str(path) + ".pre-safety-update", script)
    atomic_text(path, script.replace(old, safe).replace("# Kill any existing instance on port 5000", "# Never terminate an unrelated service"))
    path.chmod(0o700)
