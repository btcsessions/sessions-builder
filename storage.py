"""Atomic, owner-only JSON storage and recoverable multi-file restores."""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

DATA_LOCK = threading.RLock()


def locked(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with DATA_LOCK:
            return fn(*args, **kwargs)
    return wrapped


def atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        # Persist the directory entry as well as the file contents.
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@locked
def write_json(path, value):
    content = json.dumps(value, ensure_ascii=False)
    path = Path(path)
    if path.exists():
        try:
            previous = path.read_text(encoding="utf-8")
            json.loads(previous)
        except (ValueError, OSError):
            pass  # Never replace a good recovery copy with corrupt JSON.
        else:
            atomic_text(str(path) + ".bak", previous)
    atomic_text(path, content)


@locked
def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        backup = Path(str(path) + ".bak")
        if not backup.exists():
            raise ValueError(f"{path.name} is invalid and has no recovery copy") from None
        recovered = json.loads(backup.read_text(encoding="utf-8"))
        # Preserve the damaged file for manual recovery.
        atomic_text(str(path) + ".corrupt-" + uuid.uuid4().hex,
                    path.read_text(encoding="utf-8", errors="replace"))
        atomic_text(path, json.dumps(recovered))
        print(f"[Storage] Recovered {path.name} from its previous saved version.")
        return recovered


LIST_FILES = {"videos.json", "competitors.json", "title_history.json", "plans.json"}
DICT_FILES = {"settings.json", "analytics.json", "video_categories.json",
              "btc_prices.json", "snapshots.json", "trend_intel.json",
              "oauth_token.json", "nostr_history.json"}


def validate_document(name, value):
    if name not in LIST_FILES | DICT_FILES:
        raise ValueError(f"Unsupported data file: {name}")
    if not isinstance(value, list if name in LIST_FILES else dict):
        raise ValueError(f"Invalid data structure in {name}")
    if name in LIST_FILES and not all(isinstance(row, dict) for row in value):
        raise ValueError(f"Invalid records in {name}")
    if name == "plans.json":
        ids = [row.get("id") for row in value]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("Plans must have unique, nonempty IDs")
    if name == "oauth_token.json" and not value.get("token"):
        raise ValueError("OAuth credentials are missing their token")


@locked
def recover_restore(data_dir):
    """Roll back an interrupted restore before loading any application caches."""
    root = Path(data_dir)
    journal = root / ".restore-pending.json"
    if journal.exists():
        previous = json.loads(journal.read_text(encoding="utf-8"))
        for name, content in previous.items():
            if name not in LIST_FILES | DICT_FILES:
                raise ValueError("Invalid restore recovery journal")
            if content is None:
                (root / name).unlink(missing_ok=True)
            else:
                atomic_text(root / name, content)
        journal.unlink()


@locked
def restore_documents(data_dir, documents):
    """Validate everything first, retain a snapshot, and journal replacements."""
    root = Path(data_dir)
    for name, value in documents.items():
        validate_document(name, value)
    previous = {name: (root / name).read_text(encoding="utf-8")
                if (root / name).exists() else None for name in documents}
    snapshot = root / "recovery" / ("restore-" + uuid.uuid4().hex + ".json")
    atomic_text(snapshot, json.dumps(previous))
    journal = root / ".restore-pending.json"
    atomic_text(journal, json.dumps(previous))
    try:
        for name, value in documents.items():
            write_json(root / name, value)
        journal.unlink()
    except Exception:
        recover_restore(root)
        raise
    return str(snapshot)
