#!/usr/bin/env bash
set -euo pipefail
# This script does not delete .env, music/, or data/. On first v2 start, the app moves legacy top-level music into the first account's private folder.
rm -f app/playback.py scripts/render_liquidsoap.py liquidsoap/radio.liq.template \
      upgrade-v1.2.sh upgrade-v1.3.sh upgrade-v1.3.1.sh upgrade-v1.3.2.sh 2>/dev/null || true
docker compose down
docker compose build --no-cache
docker compose up -d --force-recreate
docker compose logs --tail=80 tasia-streamer
