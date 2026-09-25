# OpenLaw.in API image (Task 7: one-command self-hosting).
#
# Also used, with a `command:` override, to run
# `alembic upgrade head` as the Compose `migrate` service -- see
# docker-compose.yml. Building `migrations/` and `alembic.ini` into the same
# image the API runs from (rather than a separate migration image) keeps the
# schema a migration applies always in lockstep with the code that reads it.
FROM python:3.12-slim

WORKDIR /app

# curl: used only by this image's own Compose healthcheck
# (`curl -f http://localhost:8000/readyz`) -- see docker-compose.yml.
# gosu: lets docker-entrypoint.sh fix a bind-mounted volume's ownership as
# root, then drop to the unprivileged `openlaw` user below before running
# anything from this project (Task 7 review, M2/M3) -- confirmed available
# in this base image's own apt repo.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl gosu \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user this image actually runs as (see docker-entrypoint.sh).
# Fixed uid/gid so a bind-mounted host directory's ownership is predictable
# across rebuilds, not merely "whatever the base image's next free id is".
RUN groupadd --system --gid 1000 openlaw \
    && useradd --system --uid 1000 --gid openlaw --home-dir /app openlaw

COPY pyproject.toml ./
COPY src ./src
# `[local-nano]`: the default query embedder (`OPENLAW_QUERY_EMBEDDER_BACKEND
# =local_nano`, see .env.example) needs torch/transformers/sentence-
# transformers to run -- omitting this extra would make `production_app()`
# fail immediately on every container start with a clear but avoidable
# ImportError, since nothing here overrides the backend to `voyage_api`.
# This is the one deliberate trade a "genuinely works with zero extra
# accounts" self-host makes: a larger image (~2GB heavier) and a slower
# first build, in exchange for the API needing no Voyage key at all -- a
# legal-aid tool for people who often have neither a credit card nor an API
# account to set one up with.
#
# IMPORTANT (Task 7 review, H4): this installs the CODE that runs
# voyage-4-nano, not the model WEIGHTS. `build_query_embedder`
# (openlaw.retrieval.factory) downloads those from the Hugging Face Hub the
# first time `production_app()` builds the embedder -- i.e. before uvicorn
# ever binds a port, on the FIRST container start after a fresh build (and
# after every recreate, since nothing here caches the download -- see
# docker-compose.yml's `hf_cache` volume for that). `HF_HUB_DISABLE_XET=1`
# matches the README's existing guidance: Hugging Face's Xet download
# backend can otherwise stall indefinitely on a normal connection.
ENV HF_HUB_DISABLE_XET=1
RUN pip install --no-cache-dir -e ".[local-nano]"

# Only the curated, version-controlled glossary under `data/` -- see
# .dockerignore's docstring for why the rest of `data/` (bind-mounted
# datastore volumes, multi-GB corpus-build checkpoints) must never be baked
# into this image.
COPY data/glossary ./data/glossary
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/data \
    && chown -R openlaw:openlaw /app

# docker-entrypoint.sh runs as root just long enough to `chown` the
# bind-mounted `/app/data` volume (see its own docstring for why that
# cannot be done here, at build time), then execs the real command as
# `openlaw` -- this image never serves a request as root.
ENTRYPOINT ["docker-entrypoint.sh"]

EXPOSE 8000
CMD ["uvicorn", "openlaw.api.deps:production_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
