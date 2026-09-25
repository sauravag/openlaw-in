# OpenLaw.in

Free, open-source legal aid for India. Every legal claim is backed by a citation
that resolves to a government source document.

## Data attribution

This project is built on two open datasets. **Attribution is a licence condition,
not a courtesy** — it is imposed by upstream eCourts, NCLT and NCLAT policy.

- **[vaquill/open-india-law](https://huggingface.co/datasets/vaquill/open-india-law)**
  — Indian primary law: judgments, legislation, regulator instruments.
  Licensed **CC BY 4.0**.
- **[KanoonGPT/indian-case-laws](https://huggingface.co/datasets/KanoonGPT/indian-case-laws)**
  — structured Indian case-law metadata. Licensed **Apache-2.0**.

The underlying legal text is a Government work under s.17(d) of the Copyright Act
1957, reproducible under s.52(1)(q).

## Coverage limits

Stated plainly, because a legal tool that overstates its coverage is dangerous:

- Retrieval coverage is not corpus coverage — 1,786,868 more judgments are held as
  extracted text upstream than are embedded.
- Consumer fora (NCDRC and State Commissions) are **not** in this corpus.
- District and trial court decisions are **not** included.
- The corpus is snapshot `v2026.08` — an archive, not current law.
- **`acts_india` has a measured gap in citation coverage.** Validated against the
  live, fully-restored collection (`scripts/validate_corpus.py`, 1,098,577
  points): **137,441 points (12.5%) carry no usable government `source_url`.**
  Every one of these is a regulator instrument (`jurisdiction: "regulatory"`) —
  MCA notifications, Law Commission reports, and similar material published
  without a stable government URL in the upstream snapshot. This is inherent to
  the publisher's data, not a defect introduced by this project, and these
  points are **not** deleted from the index — deleting another party's snapshot
  data is not this project's call to make. Instead, **retrieval MUST exclude any
  chunk without a resolvable `source_url` before it is ever shown to a user or
  cited in an answer.** `acts_india` payloads also predate this project's
  `corpus_version` field entirely (it does not exist anywhere in the publisher's
  schema); this is expected and is reported by the gate, not treated as a
  failure.
- `sc_judgments_v1` (Supreme Court judgments, built by this project) is held to
  a stricter standard: every chunk that cannot resolve a government URL is
  dropped before indexing (`scripts/build_sc_index.py`), so any violation found
  there by the gate is a real defect, not an accepted gap.
- `hc_judgments_v1` holds High Court judgments and is built to the same strict
  standard as `sc_judgments_v1` (`scripts/build_hc_index.py`). It is a **shared**
  collection holding multiple High Courts, distinguished by the `court_code`
  payload field — two are onboarded (`openlaw.corpus.high_courts`): **Punjab &
  Haryana High Court** (Chandigarh; covers Punjab, Haryana, and the Chandigarh
  UT) and **Delhi High Court** (the NCT of Delhi). A High Court's judgments
  bind only within its own jurisdiction, which is why retrieval only searches
  this collection when a caller explicitly scopes a query to one of these
  jurisdictions (`openlaw.retrieval.search`). Its vectors are embedded LOCALLY
  with `voyage-4-nano` — the same model that embeds queries by default — not
  the Voyage API's `voyage-4-lite`: measured, nano queries against
  `voyage-4-lite` HC documents scored only 15.0% top-1 / 31.7% top-10 (median
  rank 61 of 300), because nano and lite's independent approximation errors
  against full `voyage-4` do not cancel against each other. `--restart` on
  `scripts/build_hc_index.py` clears only the points of the court being
  rebuilt (filtered on `court_code`), never the whole shared collection.

**This is legal information, not legal advice.** Always verify against the official
source before relying on anything here.

## Application database

`openlaw.db.models` is deliberately minimal on personal data — see its module
docstring. One table needs a specific operational precondition before it is
used:

- **`hallucination_log`** holds verbatim, potentially sensitive rejected-claim
  text — the same class of data as `openlaw.answers.escalation`'s JSONL
  hallucination log, which earns the right to keep that text verbatim by
  pairing it with `0600` file permissions and a 90-day purge
  (`purge_stale_events`). This table carries the same obligation:
  `openlaw.db.retention.purge_stale_hallucination_log` enforces the matching
  90-day window and must be run periodically once anything writes here.
  **Nothing may write to this table in production until it is reachable only
  through a restricted database role** (not the general application
  credential) — set that up first, not after.

## Setup

    cp .env.example .env
    pip install -e ".[dev,local-nano]"
    docker compose up -d
    docker compose exec postgres createdb -U openlaw openlaw_test
    alembic upgrade head
    pytest

Query embedding defaults to a local, open-weight model (`voyage-4-nano`) — no
API key needed, no per-query cost, no vendor dependency. The `local-nano` extra
above pulls in torch/transformers/sentence-transformers (~2GB) to run it; the
first query downloads the weights (set `HF_HUB_DISABLE_XET=1` if that hangs on
your network — Hugging Face's Xet backend can stall indefinitely otherwise).

Skip that ~2GB (e.g. for CI, or an API-only deployment) by installing without
the extra and switching backends:

    pip install -e ".[dev]"
    export OPENLAW_QUERY_EMBEDDER_BACKEND=voyage_api
    # and set OPENLAW_VOYAGE_API_KEY in .env

Calling the `local_nano` backend without the extra installed raises a clear
`ImportError` naming the exact `pip install -e ".[local-nano]"` command to fix it.

### Database migrations

`alembic upgrade head` (above) creates the five application tables (`users`,
`quota_usage`, `conversations`, `messages`, `hallucination_log`) in the
`openlaw` database — run it once, on every fresh Postgres, **before**
`pytest`. Migrations do not assume an empty database: if these tables
already exist some other way (an early dev workflow that predates Alembic,
or a database restored from a snapshot), reconcile with `alembic stamp head`
instead — it records the migration as applied without re-running its DDL.
Running `upgrade head` against a database that already has these tables
fails loudly with `DuplicateTable`, which is the signal to use `stamp head`
instead, not a broken migration.

`pytest`'s `@pytest.mark.integration` tests that need a live Postgres
connection target a **separate** database, `openlaw_test` by default
(`docker compose exec postgres createdb -U openlaw openlaw_test`, once;
override the URL with `OPENLAW_TEST_DATABASE_URL`) — never the `openlaw`
database above. This is deliberate: those tests create their own schema
(`Base.metadata.create_all`), and doing that in the same database Alembic
manages is exactly what breaks the migration step for a later `alembic
upgrade head`. The tests skip cleanly if `openlaw_test` isn't reachable
yet.

### Running the service

    uvicorn openlaw.api.deps:production_app --factory --host 0.0.0.0 --port 8000

`production_app()` (`openlaw.api.deps`) is the composition root: it
validates required configuration up front — failing fast with a clear
message naming exactly what is missing or misconfigured, never a stack
trace and never a service that starts only to fail on a real user's first
question — builds the query embedder and Anthropic client exactly once, and
wires them into the FastAPI app. At minimum, set `OPENLAW_ANTHROPIC_API_KEY`
in `.env`, and make an explicit reverse-proxy decision: either
`OPENLAW_TRUSTED_PROXY_IPS` (if a proxy sits in front of this service) or
`OPENLAW_NO_REVERSE_PROXY=true` (if nothing does) — see `.env.example` for
why leaving both unset is refused rather than defaulted.

## Corpus build

    python scripts/make_fixtures.py            # test fixtures (once)
    python scripts/restore_acts_india.py       # 1,098,269 provisions (~11.2 GB)
    python scripts/build_sc_index.py           # 371,247 Supreme Court chunks
    python scripts/build_hc_index.py           # ~1.85M Punjab & Haryana HC chunks (default --court)
    python scripts/build_hc_index.py --court delhi  # ~1.06M Delhi HC chunks, same shared collection
    python scripts/validate_corpus.py          # the gate: all citations resolve

`build_hc_index.py` streams and checkpoints per court (`data/hc_index_checkpoint_<court>.json`,
git-ignored) so an interrupted multi-hour run resumes with `python scripts/build_hc_index.py`
(no flag needed) rather than re-embedding from the start; `--restart` discards the
checkpoint, and `--limit N` stops after N chunks to prove the pipeline on a
bounded slice.

## Self-hosting

**This is spec success criterion 12.7: someone other than the author can
self-host this from the README alone.** `docker compose up -d` brings up the
whole *application* -- Postgres, Qdrant, Neo4j, database migrations, the API,
and the web frontend -- in one command. It does **not** bring up a working
corpus. Be clear-eyed about what that means before you start:

- **The corpus is not downloadable and does not ship in an image.** It is
  roughly **1.5 million vectors** (`acts_india_v1` + `sc_judgments_v1` +
  `hc_judgments_v1`, see "Corpus build" above), built from the two datasets
  under "Data attribution" by running this project's own scripts against
  them. There is no shortcut -- building it takes **many hours** of
  embedding (mostly network- and API-bound, not CPU-bound) and **tens of
  gigabytes of disk** (the restored `acts_india` snapshot alone is ~11.2 GB;
  budget for the built Qdrant collections on top of that). `docker compose
  up` cannot ship this for the same reason it cannot ship a Wikipedia
  mirror: it is somebody else's dataset, redistributed under CC BY
  4.0/Apache-2.0 attribution terms this project honours by pointing at the
  source, not by silently re-hosting a multi-GB derivative in a container
  registry.
- **Until that build finishes, the service runs and tells you so.** The
  application comes up immediately and answers HTTP requests; every
  question it is asked before the corpus exists comes back `unanswerable`,
  never a fabricated citation. `GET /readyz` reports this honestly (`status:
  "degraded"` until `acts_india_v1`/`sc_judgments_v1` actually hold vectors
  -- see `openlaw.api.routes._check_qdrant`), and that same check is wired
  as the `api` container's Compose healthcheck, so `docker compose ps` shows
  an incomplete self-host as unhealthy instead of looking fine right up
  until a real user's question 500s.

### Steps

1. **Copy the environment file.**

       cp .env.example .env

2. **Set the two secrets `production_app()` refuses to start without:**
   `OPENLAW_ANTHROPIC_API_KEY` (every answer -- generation, translation,
   crisis classification -- goes through it) and, if you plan to rebuild or
   extend the corpus yourself, `OPENLAW_VOYAGE_API_KEY` (only the Supreme
   Court/High Court build scripts need it; the running service's default
   query embedder is the local, open-weight `voyage-4-nano` baked into the
   `api` image, so ordinary self-hosting needs no Voyage key at all).
   `docker-compose.yml` fails immediately with a named, readable error if
   `OPENLAW_ANTHROPIC_API_KEY` is missing -- never a stack trace, never a
   service that starts and only fails on a real user's question.

3. **Build the corpus** (separately from `docker compose up` -- see above;
   this needs the Python environment on your host, not a container, since
   these scripts read the raw HuggingFace dataset snapshots directly):

       pip install -e ".[dev,local-nano]"
       python scripts/make_fixtures.py
       python scripts/restore_acts_india.py       # ~11.2 GB, tens of minutes
       python scripts/build_sc_index.py           # needs OPENLAW_VOYAGE_API_KEY
       python scripts/build_hc_index.py            # needs OPENLAW_VOYAGE_API_KEY, hours
       python scripts/build_hc_index.py --court delhi
       python scripts/validate_corpus.py           # the gate: all citations resolve

   You can skip straight to step 4 and come back to this once the
   application is confirmed up -- the two do not depend on each other.

4. **Bring up the stack.**

       docker compose up -d

   This starts Postgres, Qdrant, and Neo4j; runs `alembic upgrade head`
   against Postgres via the one-shot `migrate` service (the `api` container
   will not start until migrations succeed -- see `docker-compose.yml`); and
   starts `api` (port 8000) and `web` (port 3000, proxying `/ask` to `api`
   server-side). Migrations do not assume an empty database -- see
   "Database migrations" above for the `alembic stamp head` escape hatch if
   these tables already exist some other way.

5. **Verify it.**

       ./scripts/verify_selfhost.sh

   Expected: `[Selfhost] PASS`. This confirms the stack is up, every
   datastore is reachable, and the API and frontend are both serving --
   it does **not** require the corpus build from step 3 to be finished
   first (see that script's own comments for exactly what it does and does
   not check, and why). Re-run it after the corpus build finishes: with
   `/readyz` reporting ready, its final check asks a real question and
   verifies a genuinely cited answer comes back.

6. Open **http://localhost:3000** and ask a question.

### Quota is disabled by default for self-hosters

`OPENLAW_QUOTA_ENABLED` defaults to `false` in `docker-compose.yml` -- a
legal-aid body running its own instance should not inherit the hosted
tier's anonymous-3/signed-in-30-per-month limit. Set it to `true` (and, if
you are not behind HTTPS, leave `OPENLAW_COOKIE_SECURE=false` -- see the
next section) if you specifically want that limit on your own deployment.

**This is legal information, not legal advice.** Every answer carries this
notice and routes to DLSA/NALSA for anything beyond what a citation can
responsibly settle -- self-hosting this does not change that; see
"Coverage limits" above for exactly how far the underlying corpus goes.

### Reverse-proxy trust, and what changes if you add your own

`docker-compose.yml` sets `OPENLAW_TRUSTED_PROXY_IPS` to `web`'s own fixed
address on the Compose network (`172.28.5.10`), because `web` (Next.js)
proxies every `/ask` request to `api` server-side -- `api` never sees a
browser connect to it directly in this topology, so `web` is the one
address that needs trusting for per-caller rate limiting and the anonymous
quota to work correctly (see `docker-compose.yml`'s own comments, and
`openlaw.api.routes._client_key`, for the mechanics and what goes wrong if
this is left unset behind a real proxy). If you put your own TLS-
terminating reverse proxy in front of this stack -- required for any
deployment reachable over the public internet -- point it at `web`, not
`api`, and let it forward `X-Forwarded-For` the way every mainstream
reverse proxy does by default; no change to `OPENLAW_TRUSTED_PROXY_IPS` is
needed for that case. The one case that DOES need a change is a proxy that
talks to `api` directly (bypassing `web`'s rewrite), or a future multi-
replica `api` behind a load balancer -- set `OPENLAW_TRUSTED_PROXY_IPS` to
that proxy's/load balancer's actual address instead.

### Datastore ports never leave the host

Qdrant, Neo4j and Postgres are bound to `127.0.0.1` in `docker-compose.yml`
and must stay that way -- this is a hard project constraint, not a default
to reconsider. `api` and `web` are the only intended reachable surface.
Their default development credentials (`openlaw`/`openlaw`,
`neo4j`/`openlawdev`) are fine behind that loopback binding; override them
via `POSTGRES_PASSWORD`/`NEO4J_PASSWORD` in your `.env` before putting a
reverse proxy in front of this stack for anything beyond your own machine.
