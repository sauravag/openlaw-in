#!/usr/bin/env bash
# Entrypoint for the `api`/`migrate` image (Task 7 review, M2/M3).
#
# Runs as root only long enough to fix ownership of the bind-mounted
# `/app/data` volume, then drops to the unprivileged `openlaw` user for the
# real command. Two things force this two-step dance rather than a plain
# `USER openlaw` in the Dockerfile:
#
# - Docker creates a bind-mounted host directory that does not exist yet
#   (`./data/app` on a fresh self-host) owned by root, regardless of what a
#   build-time `chown` inside the image set that path to -- the bind mount
#   replaces the image layer at runtime. An unprivileged process would get
#   "permission denied" writing there.
# - `/app/data` is where `hallucination_log_path`/`classifier_failure_log_path`
#   (openlaw.config.Settings) write -- the safety audit trail for every
#   rejected/hallucinated claim. Losing that silently to a permission error
#   is not acceptable (Task 7 review, M2), so this must actually succeed
#   before uvicorn/alembic ever runs, not fail into a crash loop.
set -euo pipefail

mkdir -p /app/data
chown -R openlaw:openlaw /app/data

exec gosu openlaw "$@"
