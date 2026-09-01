#!/usr/bin/env bash
set -euo pipefail

mkdir -p /music /data/cache /data/users /music/users

# Host bind mounts can carry arbitrary UID/GID ownership. Tasia itself stays
# unprivileged, but its private account trees must be writable/readable by the
# liquidsoap user. This also repairs beta2 files that were moved into a user
# folder after the initial ownership pass.
chown -R liquidsoap:liquidsoap /data /music/users
find /music/users -type d -exec chmod u+rwx {} + 2>/dev/null || true
find /music/users -type f -exec chmod u+rw {} + 2>/dev/null || true

# /music itself only needs to allow creation of /music/users and one-time
# legacy migration. Do not recursively chown unrelated top-level host music.
chown liquidsoap:liquidsoap /music
chmod u+rwx /music

exec gosu liquidsoap uvicorn app.main:app --host 0.0.0.0 --port 8080
