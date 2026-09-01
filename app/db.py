from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DB_PATH, DEFAULT_STREAM


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE COLLATE NOCASE,
              display_name TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              is_admin INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_library (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              path TEXT NOT NULL,
              title TEXT NOT NULL,
              artist TEXT NOT NULL DEFAULT '',
              duration REAL,
              size_bytes INTEGER,
              folder TEXT NOT NULL DEFAULT '',
              source_kind TEXT NOT NULL DEFAULT 'local',
              source_id INTEGER,
              remote_path TEXT,
              last_played TEXT,
              added_at TEXT NOT NULL,
              UNIQUE(user_id, path)
            );
            CREATE INDEX IF NOT EXISTS idx_user_library_owner ON user_library(user_id, folder, title);
            CREATE TABLE IF NOT EXISTS user_queue (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              path TEXT NOT NULL,
              title TEXT NOT NULL,
              artist TEXT NOT NULL DEFAULT '',
              source_type TEXT NOT NULL,
              source_url TEXT,
              duration REAL,
              status TEXT NOT NULL DEFAULT 'queued',
              reserved_at TEXT,
              position INTEGER NOT NULL,
              added_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_user_queue_owner ON user_queue(user_id, position);
            CREATE TABLE IF NOT EXISTS user_playlist (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              path TEXT NOT NULL,
              title TEXT NOT NULL,
              artist TEXT NOT NULL DEFAULT '',
              source_type TEXT NOT NULL,
              source_url TEXT,
              duration REAL,
              position INTEGER NOT NULL,
              added_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_user_playlist_owner ON user_playlist(user_id, position);
            CREATE TABLE IF NOT EXISTS user_state (
              user_id INTEGER NOT NULL,
              key TEXT NOT NULL,
              value TEXT,
              PRIMARY KEY(user_id, key)
            );
            CREATE TABLE IF NOT EXISTS sources (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              name TEXT NOT NULL,
              kind TEXT NOT NULL,
              url TEXT NOT NULL DEFAULT '',
              username TEXT NOT NULL DEFAULT '',
              password TEXT NOT NULL DEFAULT '',
              root_path TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stream_settings (
              user_id INTEGER PRIMARY KEY,
              host TEXT NOT NULL,
              port INTEGER NOT NULL,
              password TEXT NOT NULL,
              sid INTEGER NOT NULL DEFAULT 1,
              name TEXT NOT NULL,
              genre TEXT NOT NULL DEFAULT '',
              url TEXT NOT NULL DEFAULT '',
              public INTEGER NOT NULL DEFAULT 0,
              bitrate INTEGER NOT NULL DEFAULT 192,
              sample_rate INTEGER NOT NULL DEFAULT 44100,
              autoplay_library INTEGER NOT NULL DEFAULT 1,
              auto_start INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_settings (
              user_id INTEGER PRIMARY KEY,
              base_url TEXT NOT NULL DEFAULT '',
              api_key TEXT NOT NULL DEFAULT '',
              model TEXT NOT NULL DEFAULT '',
              system_prompt TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS catalog_settings (
              user_id INTEGER NOT NULL,
              provider TEXT NOT NULL,
              client_id TEXT NOT NULL DEFAULT '',
              client_secret TEXT NOT NULL DEFAULT '',
              api_key TEXT NOT NULL DEFAULT '',
              bearer_token TEXT NOT NULL DEFAULT '',
              base_url TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              PRIMARY KEY(user_id, provider)
            );
            CREATE TABLE IF NOT EXISTS user_favorites (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              fingerprint TEXT NOT NULL,
              kind TEXT NOT NULL,
              provider TEXT NOT NULL DEFAULT '',
              track_id TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL,
              artist TEXT NOT NULL DEFAULT '',
              duration REAL,
              source_url TEXT NOT NULL DEFAULT '',
              artwork TEXT NOT NULL DEFAULT '',
              source_id INTEGER,
              remote_path TEXT NOT NULL DEFAULT '',
              library_id INTEGER,
              added_at TEXT NOT NULL,
              UNIQUE(user_id, fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_user_favorites_owner ON user_favorites(user_id, added_at);
            """
        )
        catalog_cols={r[1] for r in conn.execute("PRAGMA table_info(catalog_settings)")}
        if "base_url" not in catalog_cols:
            conn.execute("ALTER TABLE catalog_settings ADD COLUMN base_url TEXT NOT NULL DEFAULT ''")
        # Clear expired sessions opportunistically.
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))


def user_count() -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def create_user(username: str, display_name: str, password_hash: str, is_admin: bool = False) -> dict[str, Any]:
    username = username.strip()
    display_name = display_name.strip() or username
    if not username or len(username) < 2:
        raise ValueError("Username must be at least 2 characters")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, display_name, password_hash, is_admin, created_at) VALUES(?,?,?,?,?)",
            (username, display_name, password_hash, 1 if is_admin else 0, now_iso()),
        )
        uid = int(cur.lastrowid)
        s = DEFAULT_STREAM
        conn.execute(
            """INSERT INTO stream_settings(user_id,host,port,password,sid,name,genre,url,public,bitrate,sample_rate,autoplay_library,auto_start,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid,s["host"],s["port"],s["password"],s["sid"],s["name"],s["genre"],s["url"],1 if s["public"] else 0,
             s["bitrate"],s["sample_rate"],1 if s["autoplay_library"] else 0,1 if s["auto_start"] else 0,now_iso()),
        )
        row = conn.execute("SELECT id,username,display_name,is_admin,created_at FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row)


def find_user(username: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)).fetchone()
        return dict(row) if row else None


def user_by_id(user_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT id,username,display_name,is_admin,created_at FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT id,username,display_name,is_admin,created_at FROM users ORDER BY id")]


def update_password(user_id: int, password_hash: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def create_session(token_hash: str, user_id: int, expires_at: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (token_hash,user_id,expires_at,now_iso()))


def session_user(token_hash: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT u.id,u.username,u.display_name,u.is_admin,u.created_at
               FROM sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.expires_at>=?""", (token_hash, now_iso())
        ).fetchone()
        return dict(row) if row else None


def delete_session(token_hash: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


def set_state(user_id: int, key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO user_state(user_id,key,value) VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
            (user_id,key,payload),
        )


def get_state(user_id: int, key: str, default: Any = None) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value FROM user_state WHERE user_id=? AND key=?", (user_id,key)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default



def _path_is_under(path_value: str, root: Path) -> bool:
    try:
        Path(path_value).resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False



def _update_path_with_dedup(conn: sqlite3.Connection, table: str, user_id: int, row_id: int, new_path: Path | str) -> str:
    """Update a stored path without letting legacy duplicate library rows crash startup.

    user_library has UNIQUE(user_id, path). During beta1/beta2 migrations the same
    physical file can legitimately exist twice in SQLite: once under the stale
    /music/... path and once under the new private /music/users/... path. If both
    resolve to the same destination, keep the already-canonical row and merge the
    useful metadata from the stale row before deleting it. Queue/playlist rows are
    intentionally allowed to contain the same track multiple times.
    """
    target = str(new_path)
    if table != "user_library":
        conn.execute(f"UPDATE {table} SET path=? WHERE user_id=? AND id=?", (target, user_id, row_id))
        return "updated"

    existing = conn.execute(
        "SELECT id FROM user_library WHERE user_id=? AND path=? AND id<>? LIMIT 1",
        (user_id, target, row_id),
    ).fetchone()
    if existing is None:
        conn.execute(
            "UPDATE user_library SET path=? WHERE user_id=? AND id=?",
            (target, user_id, row_id),
        )
        return "updated"

    keep_id = int(existing["id"])
    stale = conn.execute(
        "SELECT * FROM user_library WHERE user_id=? AND id=?", (user_id, row_id)
    ).fetchone()
    if stale is not None:
        # Preserve useful metadata if the canonical row is missing it. Never move
        # the canonical row away from the valid target path.
        conn.execute(
            """UPDATE user_library SET
                   title=CASE WHEN trim(COALESCE(title,''))='' THEN ? ELSE title END,
                   artist=CASE WHEN trim(COALESCE(artist,''))='' THEN ? ELSE artist END,
                   duration=COALESCE(duration, ?),
                   size_bytes=COALESCE(size_bytes, ?),
                   folder=CASE WHEN trim(COALESCE(folder,''))='' THEN ? ELSE folder END,
                   source_id=COALESCE(source_id, ?),
                   remote_path=COALESCE(remote_path, ?),
                   last_played=COALESCE(last_played, ?),
                   added_at=CASE WHEN trim(COALESCE(added_at,''))='' THEN ? ELSE added_at END
                   WHERE user_id=? AND id=?""",
            (
                stale["title"] or Path(target).stem,
                stale["artist"] or "",
                stale["duration"],
                stale["size_bytes"],
                stale["folder"] or "",
                stale["source_id"],
                stale["remote_path"],
                stale["last_played"],
                stale["added_at"] or now_iso(),
                user_id,
                keep_id,
            ),
        )
    conn.execute("DELETE FROM user_library WHERE user_id=? AND id=?", (user_id, row_id))
    return "deduplicated"

def rewrite_music_paths(user_id: int, old_root: Path, new_root: Path, private_parent: Path) -> None:
    """Rewrite legacy /music paths for the first account after files are moved."""
    old_root = old_root.resolve(); new_root = new_root.resolve(); private_parent = private_parent.resolve()
    with connect() as conn:
        for table in ("user_library", "user_queue", "user_playlist"):
            rows = list(conn.execute(f"SELECT id,path FROM {table} WHERE user_id=?", (user_id,)))
            for row in rows:
                try:
                    path = Path(row["path"]).resolve()
                    # Never rewrite paths that were already in the new private tree.
                    if _path_is_under(str(path), private_parent):
                        continue
                    rel = path.relative_to(old_root)
                except Exception:
                    continue
                _update_path_with_dedup(conn, table, user_id, int(row["id"]), new_root / rel)


def prune_cross_user_music(user_id: int, global_music_root: Path, allowed_music_root: Path) -> dict[str, int]:
    """Remove beta1 references to another account's local /music files."""
    global_music_root = global_music_root.resolve(); allowed_music_root = allowed_music_root.resolve()
    removed = {"library": 0, "queue": 0, "playlist": 0}
    table_map = {"library": "user_library", "queue": "user_queue", "playlist": "user_playlist"}
    with connect() as conn:
        for key, table in table_map.items():
            rows = list(conn.execute(f"SELECT id,path FROM {table} WHERE user_id=?", (user_id,)))
            for row in rows:
                path = str(row["path"] or "")
                if _path_is_under(path, global_music_root) and not _path_is_under(path, allowed_music_root):
                    conn.execute(f"DELETE FROM {table} WHERE user_id=? AND id=?", (user_id, int(row["id"])))
                    removed[key] += 1
    return removed



def repair_user_music_paths(user_id: int, global_music_root: Path, allowed_music_root: Path) -> dict[str, int]:
    """Repair stale beta1/beta2 local paths after the collection moved into a private user root."""
    global_music_root = global_music_root.resolve()
    allowed_music_root = allowed_music_root.resolve()
    files = [p.resolve() for p in allowed_music_root.rglob("*") if p.is_file()] if allowed_music_root.exists() else []
    by_name: dict[str, list[Path]] = {}
    for f in files:
        by_name.setdefault(f.name.lower(), []).append(f)
    repaired = {"library": 0, "queue": 0, "playlist": 0}
    table_map = {"library": "user_library", "queue": "user_queue", "playlist": "user_playlist"}
    with connect() as conn:
        for key, table in table_map.items():
            rows = list(conn.execute(f"SELECT id,path FROM {table} WHERE user_id=?", (user_id,)))
            for row in rows:
                raw = str(row["path"] or "")
                if not raw:
                    continue

                # IMPORTANT: queue/playlist ``path`` is also used for virtual
                # provider references such as:
                #   catalog:universal:<payload>
                #   suno:<uuid>
                #   remote:<source>:<path>
                # Those are track references, not filesystem paths.  Never
                # pass them to pathlib/stat: a long encoded catalog payload can
                # otherwise raise ENAMETOOLONG during application startup.
                # This repair exists solely for legacy files that used to live
                # directly under /music, so reject everything else before any
                # filesystem operation.
                music_prefix = str(global_music_root).rstrip("/") + "/"
                if raw != str(global_music_root) and not raw.startswith(music_prefix):
                    continue

                current = Path(raw)
                try:
                    resolved = current.resolve()
                except (OSError, RuntimeError):
                    # A malformed legacy path must never prevent the web app
                    # from starting. Leave it untouched for diagnostics.
                    continue
                try:
                    exists = current.exists()
                except OSError:
                    exists = False
                if exists and _path_is_under(str(resolved), allowed_music_root):
                    continue
                # Only remap paths that came from the local /music tree. Remote
                # caches under /data must never be guessed by filename.
                if not _path_is_under(str(resolved), global_music_root):
                    continue
                candidates: list[Path] = []
                try:
                    rel = resolved.relative_to(global_music_root)
                    # beta2 may have left '/music/Foo/track.mp3' in SQLite while
                    # the file now lives under '/music/users/<account>/Foo/...'.
                    candidates.append((allowed_music_root / rel).resolve())
                    if rel.parts and rel.parts[0] == "users":
                        # If it references a different/old private prefix, fall
                        # through to basename matching below instead.
                        pass
                except Exception:
                    pass
                matches = by_name.get(current.name.lower(), [])
                if len(matches) == 1:
                    candidates.append(matches[0])
                replacement = next((c for c in candidates if c.exists() and _path_is_under(str(c), allowed_music_root)), None)
                if replacement is not None:
                    outcome = _update_path_with_dedup(conn, table, user_id, int(row["id"]), replacement)
                    repaired[key] += 1
                    if outcome == "deduplicated":
                        repaired["deduplicated"] = repaired.get("deduplicated", 0) + 1
    return repaired


def remove_missing_local_library(user_id: int, allowed_music_root: Path) -> int:
    """Drop stale local library rows only; queue/playlist rows are kept for diagnostics/repair."""
    allowed_music_root = allowed_music_root.resolve()
    removed = 0
    with connect() as conn:
        rows = list(conn.execute("SELECT id,path,source_kind FROM user_library WHERE user_id=?", (user_id,)))
        for row in rows:
            if str(row["source_kind"] or "local") != "local":
                continue
            raw = str(row["path"] or "")
            # A provider reference is not a missing local file, even if an old
            # row accidentally carries source_kind=local. Never stat it.
            allowed_prefix = str(allowed_music_root).rstrip("/") + "/"
            if raw != str(allowed_music_root) and not raw.startswith(allowed_prefix):
                continue
            path = Path(raw)
            try:
                exists = path.exists()
            except OSError:
                exists = False
            if (not exists) or (not _path_is_under(str(path), allowed_music_root)):
                conn.execute("DELETE FROM user_library WHERE user_id=? AND id=?", (user_id, int(row["id"])))
                removed += 1
    return removed

def get_stream_settings(user_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM stream_settings WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            raise KeyError("Stream settings not found")
        d = dict(row)
        d["public"] = bool(d["public"]); d["autoplay_library"] = bool(d["autoplay_library"]); d["auto_start"] = bool(d["auto_start"])
        return d


def update_stream_settings(user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    allowed = {"host","port","password","sid","name","genre","url","public","bitrate","sample_rate","autoplay_library","auto_start"}
    current = get_stream_settings(user_id)
    current.update({k:v for k,v in values.items() if k in allowed})
    with connect() as conn:
        conn.execute(
            """UPDATE stream_settings SET host=?,port=?,password=?,sid=?,name=?,genre=?,url=?,public=?,bitrate=?,sample_rate=?,autoplay_library=?,auto_start=?,updated_at=? WHERE user_id=?""",
            (str(current["host"]),int(current["port"]),str(current["password"]),int(current["sid"]),str(current["name"]),str(current["genre"]),str(current["url"]),
             1 if current["public"] else 0,int(current["bitrate"]),int(current["sample_rate"]),1 if current["autoplay_library"] else 0,1 if current["auto_start"] else 0,now_iso(),user_id)
        )
    return get_stream_settings(user_id)


def get_ai_settings(user_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM ai_settings WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {"user_id": user_id, "base_url": "", "api_key": "", "model": "", "system_prompt": "", "updated_at": ""}
        return dict(row)


def update_ai_settings(user_id: int, values: dict[str, Any], keep_api_key_if_blank: bool = True) -> dict[str, Any]:
    current = get_ai_settings(user_id)
    api_key = str(values.get("api_key") or "")
    if keep_api_key_if_blank and not api_key:
        api_key = str(current.get("api_key") or "")
    base_url = str(values.get("base_url", current.get("base_url") or "")).strip()
    model = str(values.get("model", current.get("model") or "")).strip()
    system_prompt = str(values.get("system_prompt", current.get("system_prompt") or "")).strip()
    with connect() as conn:
        conn.execute(
            """INSERT INTO ai_settings(user_id,base_url,api_key,model,system_prompt,updated_at) VALUES(?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET base_url=excluded.base_url,api_key=excluded.api_key,model=excluded.model,system_prompt=excluded.system_prompt,updated_at=excluded.updated_at""",
            (user_id, base_url, api_key, model, system_prompt, now_iso()),
        )
    return get_ai_settings(user_id)


def clear_ai_api_key(user_id: int) -> None:
    current = get_ai_settings(user_id)
    update_ai_settings(user_id, {**current, "api_key": ""}, keep_api_key_if_blank=False)


def library_tree_rows(user_id: int, folder: str = "", recursive: bool = True, query: str = "", limit: int = 100000) -> list[dict[str, Any]]:
    """Return tracks in a folder/subtree. An empty folder means the account root."""
    if query.strip():
        return list_library(user_id, query=query, limit=limit)
    wanted = folder.strip("/")
    rows = list_library(user_id, limit=limit)
    out: list[dict[str, Any]] = []
    for row in rows:
        row_folder = str(row.get("folder") or "").strip("/")
        if not wanted:
            if recursive or not row_folder:
                out.append(row)
        elif row_folder == wanted or (recursive and row_folder.startswith(wanted + "/")):
            out.append(row)
    return out


def library_stats_map(user_id: int) -> dict[str, dict[str, Any]]:
    """Aggregate recursive track counts/durations for every folder in one DB pass."""
    stats: dict[str, dict[str, Any]] = {"": {"tracks": 0, "seconds": 0.0}}
    with connect() as conn:
        rows = conn.execute("SELECT folder,duration FROM user_library WHERE user_id=?", (user_id,)).fetchall()
    for row in rows:
        folder = str(row["folder"] or "").strip("/")
        duration = float(row["duration"] or 0)
        keys = [""]
        if folder:
            parts = folder.split("/")
            keys.extend("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        for key in keys:
            item = stats.setdefault(key, {"tracks": 0, "seconds": 0.0})
            item["tracks"] += 1
            item["seconds"] += duration
    return stats


def library_folder_stats(user_id: int, folder: str = "") -> dict[str, Any]:
    return library_stats_map(user_id).get(folder.strip("/"), {"tracks": 0, "seconds": 0.0})


def upsert_library(user_id: int, path: Path, title: str, artist: str, duration: float | None, size_bytes: int | None,
                   folder: str = "", source_kind: str = "local", source_id: int | None = None, remote_path: str | None = None) -> int:
    with connect() as conn:
        conn.execute(
            """INSERT INTO user_library(user_id,path,title,artist,duration,size_bytes,folder,source_kind,source_id,remote_path,added_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,path) DO UPDATE SET title=excluded.title,artist=excluded.artist,duration=excluded.duration,size_bytes=excluded.size_bytes,
               folder=excluded.folder,source_kind=excluded.source_kind,source_id=excluded.source_id,remote_path=excluded.remote_path""",
            (user_id,str(path),title,artist,duration,size_bytes,folder,source_kind,source_id,remote_path,now_iso()),
        )
        row = conn.execute("SELECT id FROM user_library WHERE user_id=? AND path=?", (user_id,str(path))).fetchone()
        return int(row["id"])


def list_library(user_id: int, folder: str | None = None, query: str = "", limit: int = 5000) -> list[dict[str, Any]]:
    sql = "SELECT * FROM user_library WHERE user_id=?"; params: list[Any] = [user_id]
    if folder is not None:
        sql += " AND folder=?"; params.append(folder)
    if query.strip():
        sql += " AND (title LIKE ? OR artist LIKE ? OR path LIKE ?)"; q=f"%{query.strip()}%"; params += [q,q,q]
    sql += " ORDER BY lower(folder), lower(artist), lower(title) LIMIT ?"; params.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def library_folders(user_id: int) -> list[str]:
    with connect() as conn:
        return [str(r[0]) for r in conn.execute("SELECT DISTINCT folder FROM user_library WHERE user_id=? ORDER BY lower(folder)", (user_id,))]


def library_by_id(user_id: int, track_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM user_library WHERE user_id=? AND id=?", (user_id,track_id)).fetchone()
        return dict(row) if row else None


def library_by_path(user_id: int, path: Path | str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM user_library WHERE user_id=? AND path=?", (user_id,str(path))).fetchone()
        return dict(row) if row else None


def mark_library_played(user_id: int, track_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE user_library SET last_played=? WHERE user_id=? AND id=?", (now_iso(),user_id,track_id))


def _next_position(conn: sqlite3.Connection, table: str, user_id: int) -> int:
    return int(conn.execute(f"SELECT COALESCE(MAX(position),0)+1 FROM {table} WHERE user_id=?", (user_id,)).fetchone()[0])



def normalize_list_positions(user_id:int,target:str)->None:
    table={'queue':'user_queue','playlist':'user_playlist'}.get(str(target))
    if not table:
        raise ValueError('target must be queue or playlist')
    with connect() as conn:
        rows=conn.execute(f"SELECT id FROM {table} WHERE user_id=? ORDER BY position,id",(user_id,)).fetchall()
        for pos,row in enumerate(rows,1):
            conn.execute(f"UPDATE {table} SET position=? WHERE user_id=? AND id=?",(pos,user_id,int(row[0])))

def add_queue(user_id: int, path: Path | str, title: str, artist: str, source_type: str, source_url: str | None, duration: float | None) -> int:
    with connect() as conn:
        pos=_next_position(conn,"user_queue",user_id)
        cur=conn.execute("INSERT INTO user_queue(user_id,path,title,artist,source_type,source_url,duration,status,reserved_at,position,added_at) VALUES(?,?,?,?,?,?,?,'queued',NULL,?,?)",
                         (user_id,str(path),title,artist,source_type,source_url,duration,pos,now_iso()))
        return int(cur.lastrowid)


def list_queue(user_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM user_queue WHERE user_id=? ORDER BY position,id", (user_id,))]


def queue_by_id(user_id: int, item_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row=conn.execute("SELECT * FROM user_queue WHERE user_id=? AND id=?", (user_id,item_id)).fetchone()
        return dict(row) if row else None


def remove_queue(user_id: int, item_id: int) -> bool:
    with connect() as conn:
        row=conn.execute("SELECT status FROM user_queue WHERE user_id=? AND id=?", (user_id,item_id)).fetchone()
        if not row: return False
        if row["status"] == "reserved": raise ValueError("On-deck/preloaded track is locked")
        return conn.execute("DELETE FROM user_queue WHERE user_id=? AND id=?", (user_id,item_id)).rowcount > 0


def clear_queue(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM user_queue WHERE user_id=? AND status!='reserved'", (user_id,))


def reorder_queue(user_id: int, ids: list[int]) -> None:
    with connect() as conn:
        current=[dict(r) for r in conn.execute("SELECT id,status FROM user_queue WHERE user_id=? ORDER BY position,id", (user_id,))]
        cur_ids=[int(x["id"]) for x in current]
        if sorted(cur_ids) != sorted(ids): raise ValueError("Queue changed; refresh and try again")
        locked=[i for i,x in enumerate(current) if x["status"]=="reserved"]
        for idx in locked:
            if ids[idx] != cur_ids[idx]: raise ValueError("On-deck/preloaded track cannot be moved")
        for pos,item_id in enumerate(ids,1): conn.execute("UPDATE user_queue SET position=? WHERE user_id=? AND id=?", (pos,user_id,item_id))


def move_queue_to_position(user_id: int, item_id: int, target_position: int) -> int:
    """Move one queue row by visible 1-based position without crossing locked ON DECK rows."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows=[dict(r) for r in conn.execute("SELECT id,status FROM user_queue WHERE user_id=? ORDER BY position,id", (user_id,))]
        if not rows: raise ValueError("Queue is empty")
        current_idx=next((i for i,r in enumerate(rows) if int(r["id"])==int(item_id)),None)
        if current_idx is None: raise ValueError("Queue item not found")
        if rows[current_idx]["status"]=="reserved": raise ValueError("On-deck/preloaded track is locked")

        target_idx=max(0,min(len(rows)-1,int(target_position)-1))
        if target_idx==current_idx: return current_idx+1

        # Moving across a reserved row would shift that already-prefetched song's
        # visible position. Keep reserved rows as hard boundaries.
        lo=min(current_idx,target_idx); hi=max(current_idx,target_idx)
        for idx in range(lo,hi+1):
            if idx!=current_idx and rows[idx]["status"]=="reserved":
                raise ValueError(f"Position {idx+1} is locked by ON DECK")

        row=rows.pop(current_idx)
        rows.insert(target_idx,row)
        for pos,r in enumerate(rows,1):
            conn.execute("UPDATE user_queue SET position=? WHERE user_id=? AND id=?",(pos,user_id,int(r["id"])))
        return target_idx+1


def mark_queue_started(user_id: int, item_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM user_queue WHERE user_id=? AND id=?", (user_id,item_id))


def reset_reserved_queue(user_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE user_queue SET status='queued', reserved_at=NULL WHERE user_id=? AND status='reserved'", (user_id,))


def next_track(user_id: int) -> dict[str, Any] | None:
    settings=get_stream_settings(user_id)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM user_queue WHERE user_id=? AND status='queued' ORDER BY position,id LIMIT 1", (user_id,)).fetchone()
        if row:
            item=dict(row); conn.execute("UPDATE user_queue SET status='reserved',reserved_at=? WHERE id=?", (now_iso(),row["id"]))
            item.update(origin="queue",queue_id=int(row["id"]),library_id=None)
        elif settings["autoplay_library"]:
            row=conn.execute("SELECT * FROM user_library WHERE user_id=? ORDER BY CASE WHEN last_played IS NULL THEN 0 ELSE 1 END,last_played,id LIMIT 1", (user_id,)).fetchone()
            if not row: return None
            item=dict(row); conn.execute("UPDATE user_library SET last_played=? WHERE id=?", (now_iso(),row["id"]))
            item.update(source_type="library",source_url=None,origin="library",queue_id=None,library_id=int(row["id"]))
        else: return None
        return {k:item.get(k) for k in ("title","artist","path","source_type","source_url","duration","origin","queue_id","library_id")}


def list_playlist(user_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM user_playlist WHERE user_id=? ORDER BY position,id", (user_id,))]


def add_playlist(user_id: int, path: Path | str, title: str, artist: str, source_type: str, source_url: str | None, duration: float | None) -> int:
    with connect() as conn:
        pos=_next_position(conn,"user_playlist",user_id)
        cur=conn.execute("INSERT INTO user_playlist(user_id,path,title,artist,source_type,source_url,duration,position,added_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (user_id,str(path),title,artist,source_type,source_url,duration,pos,now_iso()))
        return int(cur.lastrowid)


def remove_playlist(user_id: int, item_id: int) -> bool:
    with connect() as conn: return conn.execute("DELETE FROM user_playlist WHERE user_id=? AND id=?", (user_id,item_id)).rowcount > 0


def clear_playlist(user_id: int) -> None:
    with connect() as conn: conn.execute("DELETE FROM user_playlist WHERE user_id=?", (user_id,))


def reorder_playlist(user_id: int, ids: list[int]) -> None:
    with connect() as conn:
        cur_ids=[int(r[0]) for r in conn.execute("SELECT id FROM user_playlist WHERE user_id=? ORDER BY position,id", (user_id,))]
        if sorted(cur_ids)!=sorted(ids): raise ValueError("Playlist changed; refresh and try again")
        for pos,item_id in enumerate(ids,1): conn.execute("UPDATE user_playlist SET position=? WHERE user_id=? AND id=?", (pos,user_id,item_id))


def move_playlist_to_position(user_id: int, item_id: int, target_position: int) -> int:
    """Move one playlist row to a visible 1-based position."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ids=[int(r[0]) for r in conn.execute("SELECT id FROM user_playlist WHERE user_id=? ORDER BY position,id", (user_id,))]
        if not ids: raise ValueError("Playlist is empty")
        try: current_idx=ids.index(int(item_id))
        except ValueError: raise ValueError("Playlist item not found")
        target_idx=max(0,min(len(ids)-1,int(target_position)-1))
        if target_idx==current_idx: return current_idx+1
        moved=ids.pop(current_idx); ids.insert(target_idx,moved)
        for pos,row_id in enumerate(ids,1):
            conn.execute("UPDATE user_playlist SET position=? WHERE user_id=? AND id=?",(pos,user_id,row_id))
        return target_idx+1


def playlist_by_id(user_id: int, item_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row=conn.execute("SELECT * FROM user_playlist WHERE user_id=? AND id=?", (user_id,item_id)).fetchone(); return dict(row) if row else None


def queue_all_playlist(user_id: int) -> int:
    rows=list_playlist(user_id)
    for r in rows: add_queue(user_id,Path(r["path"]),r["title"],r["artist"],r["source_type"],r.get("source_url"),r.get("duration"))
    return len(rows)


def queue_all_library(user_id: int) -> int:
    rows=list_library(user_id,limit=100000)
    for r in rows: add_queue(user_id,Path(r["path"]),r["title"],r["artist"],"library",None,r.get("duration"))
    return len(rows)


def add_source(user_id: int, name: str, kind: str, url: str, username: str, password: str, root_path: str="") -> int:
    with connect() as conn:
        cur=conn.execute("INSERT INTO sources(user_id,name,kind,url,username,password,root_path,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (user_id,name.strip(),kind,url.strip(),username.strip(),password,root_path.strip(),now_iso()))
        return int(cur.lastrowid)


def list_sources(user_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows=[dict(r) for r in conn.execute("SELECT id,user_id,name,kind,url,username,root_path,created_at FROM sources WHERE user_id=? ORDER BY name", (user_id,))]
        return rows


def source_by_id(user_id: int, source_id: int, with_password: bool=True) -> dict[str, Any] | None:
    cols="*" if with_password else "id,user_id,name,kind,url,username,root_path,created_at"
    with connect() as conn:
        row=conn.execute(f"SELECT {cols} FROM sources WHERE user_id=? AND id=?", (user_id,source_id)).fetchone(); return dict(row) if row else None


def delete_source(user_id: int, source_id: int) -> bool:
    with connect() as conn: return conn.execute("DELETE FROM sources WHERE user_id=? AND id=?", (user_id,source_id)).rowcount>0


def upsert_favorite(user_id: int, *, fingerprint: str, kind: str, provider: str = "", track_id: str = "",
                    title: str, artist: str = "", duration: float | None = None, source_url: str = "", artwork: str = "",
                    source_id: int | None = None, remote_path: str = "", library_id: int | None = None) -> dict[str, Any]:
    fingerprint = str(fingerprint).strip()
    if not fingerprint:
        raise ValueError("Favorite fingerprint cannot be empty")
    with connect() as conn:
        conn.execute(
            """INSERT INTO user_favorites(user_id,fingerprint,kind,provider,track_id,title,artist,duration,source_url,artwork,source_id,remote_path,library_id,added_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,fingerprint) DO UPDATE SET
                 kind=excluded.kind,provider=excluded.provider,track_id=excluded.track_id,title=excluded.title,artist=excluded.artist,
                 duration=COALESCE(excluded.duration,user_favorites.duration),source_url=excluded.source_url,artwork=excluded.artwork,
                 source_id=excluded.source_id,remote_path=excluded.remote_path,library_id=excluded.library_id""",
            (user_id,fingerprint,kind,provider,track_id,title.strip() or "Untitled",artist.strip(),duration,source_url,artwork,source_id,remote_path,library_id,now_iso()),
        )
        row=conn.execute("SELECT * FROM user_favorites WHERE user_id=? AND fingerprint=?",(user_id,fingerprint)).fetchone()
        return dict(row)


def list_favorites(user_id: int, query: str = "", limit: int = 5000) -> list[dict[str, Any]]:
    sql="SELECT * FROM user_favorites WHERE user_id=?"; params:list[Any]=[user_id]
    if query.strip():
        q=f"%{query.strip()}%"
        sql += " AND (title LIKE ? OR artist LIKE ? OR provider LIKE ? OR source_url LIKE ? OR remote_path LIKE ?)"
        params += [q,q,q,q,q]
    sql += " ORDER BY lower(artist), lower(title), id DESC LIMIT ?"; params.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql,params)]


def favorite_by_id(user_id: int, favorite_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row=conn.execute("SELECT * FROM user_favorites WHERE user_id=? AND id=?",(user_id,favorite_id)).fetchone()
        return dict(row) if row else None


def remove_favorite(user_id: int, favorite_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM user_favorites WHERE user_id=? AND id=?",(user_id,favorite_id)).rowcount > 0


def adopt_legacy_for_first_user(user_id: int) -> dict[str,int]:
    """Copy v1.x global tables into the first v2 account once. Original tables stay untouched."""
    counts={"library":0,"queue":0,"playlist":0}
    with connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM user_library WHERE user_id=?",(user_id,)).fetchone()[0]: return counts
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "library" in tables:
            for r in conn.execute("SELECT * FROM library ORDER BY id"):
                d=dict(r); folder=""
                try:
                    p=Path(d["path"]); folder=str(p.parent)
                except Exception: pass
                conn.execute("INSERT OR IGNORE INTO user_library(user_id,path,title,artist,duration,size_bytes,folder,source_kind,last_played,added_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (user_id,d["path"],d.get("title") or Path(d["path"]).stem,d.get("artist") or "",d.get("duration"),d.get("size_bytes"),folder,"local",d.get("last_played"),d.get("added_at") or now_iso())); counts["library"]+=1
        if "queue" in tables:
            pos=1
            for r in conn.execute("SELECT * FROM queue ORDER BY position,id"):
                d=dict(r); conn.execute("INSERT INTO user_queue(user_id,path,title,artist,source_type,source_url,duration,status,reserved_at,position,added_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id,d["path"],d.get("title") or "Untitled",d.get("artist") or "",d.get("source_type") or "legacy",d.get("source_url"),d.get("duration"),"queued",None,pos,d.get("added_at") or now_iso())); pos+=1; counts["queue"]+=1
        if "playlist" in tables:
            pos=1
            for r in conn.execute("SELECT * FROM playlist ORDER BY position,id"):
                d=dict(r); conn.execute("INSERT INTO user_playlist(user_id,path,title,artist,source_type,source_url,duration,position,added_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (user_id,d["path"],d.get("title") or "Untitled",d.get("artist") or "",d.get("source_type") or "legacy",d.get("source_url"),d.get("duration"),pos,d.get("added_at") or now_iso())); pos+=1; counts["playlist"]+=1
    return counts


def get_catalog_settings(user_id: int, provider: str) -> dict[str, Any]:
    provider=provider.strip().lower()
    with connect() as conn:
        row=conn.execute("SELECT * FROM catalog_settings WHERE user_id=? AND provider=?", (user_id,provider)).fetchone()
    if row: return dict(row)
    return {"user_id":user_id,"provider":provider,"client_id":"","client_secret":"","api_key":"","bearer_token":"","base_url":""}


def list_catalog_settings(user_id: int) -> list[dict[str, Any]]:
    providers=("universal","soundcloud","audius","jamendo","stremio")
    return [get_catalog_settings(user_id,p) for p in providers]


def update_catalog_settings(user_id: int, provider: str, values: dict[str, Any], keep_secrets_if_blank: bool=True) -> dict[str, Any]:
    provider=provider.strip().lower()
    if provider not in {"universal","soundcloud","audius","jamendo","stremio"}: raise ValueError("Unsupported catalog provider")
    old=get_catalog_settings(user_id,provider)
    client_id=str(values.get("client_id") or "").strip()
    client_secret=str(values.get("client_secret") or "")
    api_key=str(values.get("api_key") or "").strip()
    bearer_token=str(values.get("bearer_token") or "")
    base_url=str(values.get("base_url") or "").strip()
    if keep_secrets_if_blank:
        if not client_secret: client_secret=str(old.get("client_secret") or "")
        if not bearer_token: bearer_token=str(old.get("bearer_token") or "")
    with connect() as conn:
        conn.execute("""INSERT INTO catalog_settings(user_id,provider,client_id,client_secret,api_key,bearer_token,base_url,updated_at) VALUES(?,?,?,?,?,?,?,?)
                     ON CONFLICT(user_id,provider) DO UPDATE SET client_id=excluded.client_id,client_secret=excluded.client_secret,api_key=excluded.api_key,bearer_token=excluded.bearer_token,base_url=excluded.base_url,updated_at=excluded.updated_at""",
                     (user_id,provider,client_id,client_secret,api_key,bearer_token,base_url,now_iso()))
    return get_catalog_settings(user_id,provider)
