#!/usr/bin/env bash
# Entrypoint for the `api`/`migrate` image (Task 7 review, M2/M3).
#
# Runs as root only long enough to fix ownership of the bind-mounted
# `/app/var` volume, then drops to the unprivileged `openlaw` user for the
# real command. Two things force this two-step dance rather than a plain
# `USER openlaw` in the Dockerfile:
#
# - Docker creates a bind-mounted host directory that does not exist yet
#   (`./data/app` on a fresh self-host) owned by root, regardless of what a
#   build-time `chown` inside the image set that path to -- the bind mount
#   replaces the image layer at runtime. An unprivileged process would get
#   "permission denied" writing there.
# - `/app/var` is where `hallucination_log_path`/`classifier_failure_log_path`
#   (openlaw.config.Settings) write -- the safety audit trail for every
#   rejected/hallucinated claim. Losing that silently to a permission error
#   is not acceptable (Task 7 review, M2), so this must actually succeed
#   before uvicorn/alembic ever runs, not fail into a crash loop.
# The mount point is `/app/var`, deliberately NOT `/app/data`. `/app/data`
# holds `data/glossary/legal_terms.json`, baked into the image at build
# time; bind-mounting a host directory over `/app/data` shadowed the whole
# directory, so `load_glossary()` raised FileNotFoundError and the API
# crash-looped on startup. Mutable runtime state and immutable baked-in
# assets must not share a mount point.
set -euo pipefail

mkdir -p /app/var
chown -R openlaw:openlaw /app/var

# The same root-owned-mount problem, for the `hf_cache` named volume that
# Task 7's H4 fix added to hold the voyage-4-nano weights. Docker creates a
# fresh named volume owned by root, so the unprivileged `openlaw` user could
# not write the downloaded weights and the API crash-looped on
# `PermissionError: /app/.cache/huggingface/hub`. Found only by running the
# stack: the volume and the drop to non-root were added by two separate
# fixes (H4 and M3) that were each correct alone.
#
# Non-recursive on purpose. This cache holds multi-gigabyte model weights,
# and a recursive chown on every container start would add minutes to boot
# for no benefit -- owning the directory is enough for the files created
# inside it afterwards.
mkdir -p /app/.cache/huggingface
chown openlaw:openlaw /app/.cache /app/.cache/huggingface

exec gosu openlaw "$@"
