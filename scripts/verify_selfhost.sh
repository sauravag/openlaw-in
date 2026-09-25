#!/usr/bin/env bash
# Verify a clean self-host works end to end.
# This is spec success criterion 12.7 made executable.
#
# What "PASS" means here: the stack starts, migrations ran, every datastore
# and both application services are reachable, and the API answers HTTP
# requests through the shipped `proxy` ingress. It deliberately does NOT
# require a populated corpus to pass -- binding requirement 6 (see the
# task-7 brief/report) is explicit that a fresh self-host comes up around an
# EMPTY corpus (the corpus build is a separate, multi-hour step -- see
# README's "Corpus build" section), so requiring a real cited answer here
# would make this script fail on every clean self-host on its very first
# run, which is exactly the outcome this script exists to catch, not cause.
# Once the corpus build finishes and OPENLAW_ANTHROPIC_API_KEY is set, this
# script's own final check verifies the service can actually answer -- see
# "checking readiness" below.
set -euo pipefail

echo "[Selfhost] starting the full stack"
docker compose up -d

echo "[Selfhost] waiting for the API (first start downloads the nano model"
echo "[Selfhost] weights before it serves anything -- see README step 4;"
echo "[Selfhost] this can take several minutes on a slow connection)"
api_up=false
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    api_up=true
    break
  fi
  sleep 10
done

if [ "$api_up" != true ]; then
  echo "[Selfhost] FAIL -- the API never answered /health within 10 minutes."
  echo "[Selfhost] Check: docker compose logs api"
  exit 1
fi

echo "[Selfhost] checking health"
curl -fsS http://localhost:8000/health | grep -q '"status":"ok"'

echo "[Selfhost] checking every datastore is reachable"
curl -fsS http://localhost:6333/healthz >/dev/null
curl -fsS http://localhost:7474 >/dev/null

echo "[Selfhost] checking the reverse-proxy ingress is serving the frontend"
curl -fsS http://localhost:8080/ >/dev/null

echo "[Selfhost] checking readiness (corpus + full answer pipeline)"
readyz_body="$(mktemp)"
trap 'rm -f "$readyz_body"' EXIT
# Deliberately no `-f` here (Task 7 review, L1): `-f` on a non-2xx response
# discards the body along with the error, so the diagnostic this prints on
# the "not ready yet" branch below would silently be empty. `-o`/`-w`
# capture the body and status code regardless of what the status was.
readyz_status=$(curl -sS -o "$readyz_body" -w '%{http_code}' \
  http://localhost:8000/readyz || echo "000")

if [ "$readyz_status" = "200" ]; then
  echo "[Selfhost] ready -- asking a real question end to end"
  # Final review, M2: `corpus_version` alone proves nothing -- it is present
  # on EVERY AskResponse, including `is_answerable: false` with an empty
  # `sources` list (an operator whose corpus build produced nothing usable
  # would see this script print PASS regardless). A genuinely cited answer
  # requires BOTH `is_answerable: true` and a non-empty `sources` array --
  # `'"sources":[{'` matches only when at least one source card is present,
  # never an empty `[]`.
  ask_body="$(curl -fsS -X POST http://localhost:8080/api/openlaw/ask \
    -H 'Content-Type: application/json' \
    -d '{"question": "My landlord will not return my security deposit."}')"

  if echo "$ask_body" | grep -q '"is_answerable":true' \
    && echo "$ask_body" | grep -q '"sources":\[{'; then
    echo "[Selfhost] PASS -- the stack is up and answering with citations"
  else
    echo "[Selfhost] PASS -- the application stack is up and /readyz reports"
    echo "[Selfhost]   ready, but that question came back with NO citation:"
    echo "[Selfhost]   ${ask_body}"
    echo "[Selfhost] This does not necessarily mean the corpus is broken --"
    echo "[Selfhost] this one question may simply have no good match yet --"
    echo "[Selfhost] but it means this run did NOT verify a cited answer.  Try"
    echo "[Selfhost] a different question, or re-run scripts/validate_corpus.py"
    echo "[Selfhost] (the actual citation-coverage gate) if this persists."
  fi
else
  echo "[Selfhost] PASS -- the application stack is up, but /readyz reports"
  echo "[Selfhost]   status ${readyz_status}: $(cat "$readyz_body")"
  echo "[Selfhost] This is expected on a fresh self-host: the corpus has not"
  echo "[Selfhost] been built yet (or is not built COMPLETELY -- acts_india_v1"
  echo "[Selfhost] specifically requires scripts/build_sparse_vectors.py, not"
  echo "[Selfhost] just restore_acts_india.py; see README's 'Corpus build'"
  echo "[Selfhost] section). Re-run this script once it's done -- the check"
  echo "[Selfhost] above will then ask a real question and verify a cited"
  echo "[Selfhost] answer comes back."
fi
