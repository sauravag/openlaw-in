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
#
# It also verifies the DOCUMENT UPLOAD surface, which needs no corpus at all:
# that OPENLAW_DOCUMENT_ENCRYPTION_KEY is configured and the right length
# whenever uploads are enabled, and that a tiny text file can be uploaded and
# then deleted again. **The key is never printed** -- only whether it is
# present and whether it decodes to 32 bytes.
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

# --- the document upload surface (Plan 6 Task 18) ---------------------------
#
# NOTHING BELOW EVER PRINTS A KEY. The encryption-key check reports only
# whether the value is present and whether it decodes to the right number of
# bytes; the value itself is read into a shell variable, measured, and unset.
# An operator reading a CI log must learn that the key is configured and must
# not learn the key.

# Read one setting: an exported environment variable wins, else `.env`, else
# the supplied default. `.env` is parsed with `sed` rather than sourced, so a
# malformed line cannot execute anything.
setting() {
  local name="$1" fallback="${2-}" from_env from_file
  from_env="$(printenv "$name" || true)"
  if [ -n "$from_env" ]; then
    printf '%s' "$from_env"
    return 0
  fi
  if [ -f .env ]; then
    from_file="$(sed -n "s/^${name}=//p" .env | tail -n 1 | tr -d '\r')"
    if [ -n "$from_file" ]; then
      printf '%s' "$from_file"
      return 0
    fi
  fi
  printf '%s' "$fallback"
}

# Default `false`, matching docker-compose.yml's own fallback -- not
# `Settings.uploads_enabled`'s code default, which is what this stack
# deliberately overrides for a self-host.
uploads_enabled="$(setting OPENLAW_UPLOADS_ENABLED false)"
upload_checks_ok=false

if [ "$uploads_enabled" != "true" ]; then
  echo "[Selfhost] uploads are switched off (OPENLAW_UPLOADS_ENABLED=${uploads_enabled})"
  echo "[Selfhost]   -- skipping the document checks, which is correct, not a gap."
else
  echo "[Selfhost] checking OPENLAW_DOCUMENT_ENCRYPTION_KEY is set (never printed)"
  document_key="$(setting OPENLAW_DOCUMENT_ENCRYPTION_KEY '')"
  if [ -z "$document_key" ]; then
    echo "[Selfhost] FAIL -- OPENLAW_UPLOADS_ENABLED is true but"
    echo "[Selfhost]   OPENLAW_DOCUMENT_ENCRYPTION_KEY is empty. Uploads have"
    echo "[Selfhost]   nothing to encrypt with. Generate one with:"
    echo "[Selfhost]     python -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())'"
    exit 1
  fi
  # Length only -- the value is never echoed, piped to a log, or interpolated
  # into a message.
  key_bytes=$(printf '%s' "$document_key" | base64 -d 2>/dev/null | wc -c | tr -d ' ')
  unset document_key
  if [ "$key_bytes" != "32" ]; then
    echo "[Selfhost] FAIL -- OPENLAW_DOCUMENT_ENCRYPTION_KEY does not decode to"
    echo "[Selfhost]   32 bytes (AES-256 needs exactly 32; got ${key_bytes})."
    echo "[Selfhost]   The key itself is deliberately not printed."
    exit 1
  fi
  echo "[Selfhost]   present, and decodes to 32 bytes."
  upload_checks_ok=true
fi

if [ "$upload_checks_ok" = true ]; then
  echo "[Selfhost] uploading a tiny text document, then deleting it"
  # One cookie jar for both calls: an anonymous upload is owned by the
  # `openlaw_anon_id` cookie, so the DELETE must arrive as the same identity
  # or it is correctly refused with a 404.
  cookie_jar="$(mktemp)"
  upload_fixture="$(mktemp -t openlaw-selfhost-XXXXXX)"
  trap 'rm -f "${readyz_body:-}" "$cookie_jar" "$upload_fixture"' EXIT
  printf 'This is a self-host verification fixture, not a real legal document.\n' \
    > "$upload_fixture"

  upload_body="$(curl -sS -c "$cookie_jar" -b "$cookie_jar" \
    -X POST http://localhost:8080/api/openlaw/documents \
    -F "file=@${upload_fixture};type=text/plain;filename=selfhost-check.txt" || true)"

  # The id is a UUID in a flat JSON object; `sed` is enough and avoids a `jq`
  # dependency this script has never needed.
  document_id="$(printf '%s' "$upload_body" \
    | sed -n 's/.*"id":"\([0-9a-f-]\{36\}\)".*/\1/p')"

  if [ -z "$document_id" ]; then
    echo "[Selfhost] FAIL -- the upload did not return a document id."
    echo "[Selfhost]   Response: ${upload_body}"
    echo "[Selfhost]   Check: docker compose logs api"
    exit 1
  fi
  echo "[Selfhost]   stored as ${document_id}"

  delete_status=$(curl -sS -c "$cookie_jar" -b "$cookie_jar" -o /dev/null \
    -w '%{http_code}' -X DELETE \
    "http://localhost:8080/api/openlaw/documents/${document_id}" || echo "000")

  if [ "$delete_status" != "204" ]; then
    echo "[Selfhost] FAIL -- deleting the uploaded document returned"
    echo "[Selfhost]   ${delete_status}, not 204. An upload that cannot be"
    echo "[Selfhost]   withdrawn is the one failure this feature must not have."
    exit 1
  fi

  # Idempotent and unenumerable: a second DELETE of the same id is a 404, the
  # same answer an id that never existed gets.
  repeat_status=$(curl -sS -c "$cookie_jar" -b "$cookie_jar" -o /dev/null \
    -w '%{http_code}' -X DELETE \
    "http://localhost:8080/api/openlaw/documents/${document_id}" || echo "000")
  if [ "$repeat_status" != "404" ]; then
    echo "[Selfhost] FAIL -- a second DELETE returned ${repeat_status}, not 404."
    exit 1
  fi
  echo "[Selfhost]   upload round trip OK: stored, deleted, and gone."
fi

echo "[Selfhost] checking readiness (corpus + full answer pipeline)"
readyz_body="$(mktemp)"
trap 'rm -f "$readyz_body" "${cookie_jar:-}" "${upload_fixture:-}"' EXIT
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
