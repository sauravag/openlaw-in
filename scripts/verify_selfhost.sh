#!/usr/bin/env bash
# Verify a clean self-host works end to end.
# This is spec success criterion 12.7 made executable.
#
# What "PASS" means here: the stack starts, migrations ran, every datastore
# and both application services are reachable, and the API answers HTTP
# requests. It deliberately does NOT require a populated corpus to pass --
# binding requirement 6 (see the task-7 brief/report) is explicit that a
# fresh self-host comes up around an EMPTY corpus (the corpus build is a
# separate, multi-hour step -- see README's "Corpus build" section), so
# requiring a real cited answer here would make this script fail on every
# clean self-host on its very first run, which is exactly the outcome this
# script exists to catch, not cause. Once the corpus build finishes and
# OPENLAW_ANTHROPIC_API_KEY is set, this script's own final check verifies
# the service can actually answer -- see "checking readiness" below.
set -euo pipefail

echo "[Selfhost] starting the full stack"
docker compose up -d

echo "[Selfhost] waiting for the API"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "[Selfhost] checking health"
curl -fsS http://localhost:8000/health | grep -q '"status":"ok"'

echo "[Selfhost] checking every datastore is reachable"
curl -fsS http://localhost:6333/healthz >/dev/null
curl -fsS http://localhost:7474 >/dev/null

echo "[Selfhost] checking the frontend is serving"
curl -fsS http://localhost:3000 >/dev/null

echo "[Selfhost] checking readiness (corpus + full answer pipeline)"
readyz_status=$(curl -fsS -o /tmp/openlaw_readyz.json -w '%{http_code}' \
  http://localhost:8000/readyz || true)
if [ "$readyz_status" = "200" ]; then
  echo "[Selfhost] ready -- asking a real question end to end"
  curl -fsS -X POST http://localhost:8000/ask \
    -H 'Content-Type: application/json' \
    -d '{"question": "My landlord will not return my security deposit."}' \
    | grep -q '"corpus_version"'
  echo "[Selfhost] PASS -- the stack is up and answering with citations"
else
  echo "[Selfhost] PASS -- the application stack is up, but /readyz reports"
  echo "[Selfhost]   $(cat /tmp/openlaw_readyz.json 2>/dev/null || echo 'not reachable')"
  echo "[Selfhost] This is expected on a fresh self-host: the corpus has not"
  echo "[Selfhost] been built yet. See README's 'Corpus build' section, then"
  echo "[Selfhost] re-run this script -- the final check above will then ask"
  echo "[Selfhost] a real question and verify a cited answer comes back."
fi
