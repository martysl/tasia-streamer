FROM savonet/liquidsoap:v2.4.5

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-venv ffmpeg curl ca-certificates gosu unzip gnupg \
       openssl aria2 g++ git libffi-dev zlib1g-dev build-essential \
       libc6 libgcc-s1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# btch-downloader 6.3.6 requires Node.js >= 20.18.1.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && node --version \
    && npm --version \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp requires an external JS runtime for full YouTube support. Deno is
# the runtime recommended by yt-dlp and is enabled automatically when on PATH.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

WORKDIR /app
COPY node/package.json /app/node/package.json
RUN cd /app/node && npm install --omit=dev
COPY node/btch-helper.mjs /app/node/btch-helper.mjs
COPY requirements.txt /app/requirements.txt

# Match spotDL's Docker runtime/build dependencies and keep the Python setup in
# separate layers so BuildKit reports the exact failing stage if SpotipyFree's
# native SpotAPI/curl_cffi stack cannot load.
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel
RUN /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt \
    && /opt/venv/bin/pip check
RUN /opt/venv/bin/python -c "import importlib.metadata as m; print('spotipyFree', m.version('spotipyFree')); print('spotapi', m.version('spotapi')); import requests; from SpotipyFree import Spotify; Spotify(); print('SpotipyFree import OK')"

COPY app /app/app
COPY extras /app/extras
COPY liquidsoap /app/liquidsoap
COPY scripts /app/scripts
RUN chmod +x /app/scripts/start.sh \
    && mkdir -p /music /data/cache /data/users \
    && chown -R liquidsoap:liquidsoap /app /data

ENV PATH="/opt/venv/bin:${PATH}" PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["/app/scripts/start.sh"]
