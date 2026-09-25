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
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

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
# account to set one up with. `HF_HUB_DISABLE_XET=1` below matches the
# README's existing guidance: the weights download on first use, and
# Hugging Face's Xet backend can otherwise stall indefinitely.
ENV HF_HUB_DISABLE_XET=1
RUN pip install --no-cache-dir -e ".[local-nano]"

# Only the curated, version-controlled glossary under `data/` -- see
# .dockerignore's docstring for why the rest of `data/` (bind-mounted
# datastore volumes, multi-GB corpus-build checkpoints) must never be baked
# into this image.
COPY data/glossary ./data/glossary
COPY migrations ./migrations
COPY alembic.ini ./

EXPOSE 8000
CMD ["uvicorn", "openlaw.api.deps:production_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
