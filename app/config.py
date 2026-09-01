from __future__ import annotations

import os
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DB_PATH = Path(os.getenv("DB_PATH", "/data/tasia.db"))
MUSIC_DIR = Path(os.getenv("MUSIC_DIR", "/music"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", "/data/cache"))
USER_DATA_DIR = Path(os.getenv("USER_DATA_DIR", "/data/users"))
ALLOW_PRIVATE_URLS = env_bool("ALLOW_PRIVATE_URLS", False)
ALLOW_REGISTRATION = env_bool("ALLOW_REGISTRATION", False)
MAX_REMOTE_BYTES = int(float(os.getenv("MAX_REMOTE_MB", "250")) * 1024 * 1024)
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
APP_SECRET = os.getenv("APP_SECRET", "").strip()

# Legacy/default values are used when the first account adopts a v1.x install.
DEFAULT_STREAM = {
    "host": os.getenv("SHOUTCAST_HOST", "127.0.0.1"),
    "port": int(os.getenv("SHOUTCAST_PORT", "8000")),
    "password": os.getenv("SHOUTCAST_PASSWORD", "change-me"),
    "sid": int(os.getenv("SHOUTCAST_SID", "1")),
    "name": os.getenv("STREAM_NAME", "Tasia Radio"),
    "genre": os.getenv("STREAM_GENRE", "AI Music"),
    "url": os.getenv("STREAM_URL", ""),
    "public": env_bool("STREAM_PUBLIC", False),
    "bitrate": int(os.getenv("BITRATE_KBPS", "192")),
    "sample_rate": int(os.getenv("SAMPLE_RATE", "44100")),
    "autoplay_library": env_bool("AUTOPLAY_LIBRARY", True),
    "auto_start": False,
}

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma"}
