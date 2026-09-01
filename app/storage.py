from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Iterable

from .config import MUSIC_DIR, USER_DATA_DIR

PRIVATE_MUSIC_PARENT = MUSIC_DIR / "users"
MIGRATION_MARKER = USER_DATA_DIR / ".private-music-beta2-migrated"


def _slug(username: str, user_id: int) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", (username or "").strip()).strip("._-")
    if not clean:
        clean = "user"
    return f"{int(user_id)}-{clean}"


def user_music_root(user: dict) -> Path:
    return PRIVATE_MUSIC_PARENT / _slug(str(user.get("username") or "user"), int(user["id"]))


def ensure_user_storage(user: dict) -> Path:
    root = user_music_root(user)
    root.mkdir(parents=True, exist_ok=True)
    (root / "Uploads").mkdir(parents=True, exist_ok=True)
    user_data = USER_DATA_DIR / str(user["id"])
    for name in ("cache", "remote", "engines"):
        (user_data / name).mkdir(parents=True, exist_ok=True)
    return root


def _move_entry(src: Path, dst: Path) -> None:
    """Move a legacy top-level entry into the first user's private root."""
    if not dst.exists():
        shutil.move(str(src), str(dst))
        return
    if src.is_dir() and dst.is_dir():
        for child in list(src.iterdir()):
            _move_entry(child, dst / child.name)
        try:
            src.rmdir()
        except OSError:
            pass
        return
    # A collision should be rare. Preserve both rather than overwrite audio.
    stem, suffix = dst.stem, dst.suffix
    n = 1
    candidate = dst.with_name(f"{stem}-legacy-{n}{suffix}")
    while candidate.exists():
        n += 1
        candidate = dst.with_name(f"{stem}-legacy-{n}{suffix}")
    shutil.move(str(src), str(candidate))


def migrate_legacy_shared_music(users: Iterable[dict], db_module) -> dict:
    """
    One-time beta1/v1 migration.

    Old builds treated /music as a collection visible to every account. Beta2
    gives every account its own /music/users/<id>-<username> root. Existing
    top-level content becomes the first account's private collection.
    """
    users = sorted(list(users), key=lambda u: int(u["id"]))
    if not users or MIGRATION_MARKER.exists():
        return {"migrated": False, "moved": 0}

    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_MUSIC_PARENT.mkdir(parents=True, exist_ok=True)
    owner = users[0]
    owner_root = ensure_user_storage(owner)

    entries = [p for p in MUSIC_DIR.iterdir() if p.name not in {PRIVATE_MUSIC_PARENT.name, ".gitkeep"}]
    moved = 0
    for entry in entries:
        _move_entry(entry, owner_root / entry.name)
        moved += 1

    # Rewrite the first user's database references from /music/foo ->
    # /music/users/<owner>/foo. Other beta1 users lose references to the old
    # shared tree so they cannot play/browse the owner's files.
    db_module.rewrite_music_paths(int(owner["id"]), MUSIC_DIR, owner_root, PRIVATE_MUSIC_PARENT)
    for user in users:
        root = ensure_user_storage(user)
        db_module.prune_cross_user_music(int(user["id"]), MUSIC_DIR, root)

    MIGRATION_MARKER.parent.mkdir(parents=True, exist_ok=True)
    MIGRATION_MARKER.write_text(
        f"owner_id={owner['id']}\nowner={owner.get('username','')}\nroot={owner_root}\nmoved_top_level={moved}\n",
        encoding="utf-8",
    )
    return {"migrated": True, "moved": moved, "owner_id": int(owner["id"]), "root": str(owner_root)}


def repair_user_storage(user: dict, db_module) -> dict:
    """Repair paths left behind by beta1/beta2 and make the private tree usable."""
    root = ensure_user_storage(user)
    repaired = db_module.repair_user_music_paths(int(user["id"]), MUSIC_DIR, root)
    pruned = db_module.prune_cross_user_music(int(user["id"]), MUSIC_DIR, root)
    return {"root": str(root), "repaired": repaired, "pruned": pruned}
