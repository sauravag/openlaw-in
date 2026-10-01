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

- **"Backed by a citation" means the citation resolves, not that the sentence
  was checked against it.** The validator (`openlaw.answers.validate`) proves
  that every chunk a claim cites was retrieved and shown to the model on that
  same turn, and that it carries a resolvable government `source_url`. It does
  **not** check entailment: nothing verifies that the claim's wording is what
  that chunk actually says. So an unsupported *citation* is structurally
  impossible, while a claim that misstates a provision it correctly cites is
  not. That is the strongest honest reading of the line at the top of this
  file, and it is why every answer carries the source card: the citation is
  there to be read, not merely counted. There is no entailment check anywhere
  in this pipeline, in either the corpus or the uploaded-document namespace.
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
  hallucination log. Both earn the right to keep that text verbatim by
  pairing it with two controls: `0600` file permissions (the JSONL logs) and
  a 90-day retention window (`purge_stale_events` /
  `openlaw.db.retention.purge_stale_hallucination_log`). **Neither control
  runs itself** — see "Retention" immediately below for the executable
  mechanism and exactly how to schedule it.
  **Nothing may write to this table in production until it is reachable only
  through a restricted database role** (not the general application
  credential) — set that up first, not after. (This table currently has no
  writer anywhere in shipped code — see "Reserved, currently-unused tables"
  further down.)

### Retention

**The rule is asymmetric, not a single age cutoff: an authenticated user's
uploads and conversations are kept until the user deletes them; an
anonymous upload or conversation is kept only for a bounded number of
hours.** The anonymous path exists so someone asking about domestic violence
need not attach an email — and a cookie-keyed identity cannot reliably come
back later and delete anything itself, so this project bounds its lifetime
on a timer instead of leaving it to the person who may never return. An
undeletable FIR or eviction notice belonging to nobody is exactly the
outcome this is designed to prevent. Concretely:

- `documents.expires_at` / `conversations.expires_at` are `NULL` for an
  authenticated upload or conversation — **no purge below ever touches a
  `NULL` row, under any configuration.** An anonymous row instead gets
  `now + Settings.anonymous_upload_ttl_hours` /
  `+ anonymous_conversation_ttl_hours` (both default **24 hours**) at write
  time, and both columns are indexed so an expired-row scan is cheap.
- Purging a conversation cascades to its messages and every document filed
  under it (and each of those documents' excerpts), regardless of that
  document's own `expires_at` — a conversation's lifetime bounds everything
  in it.
- **Document access-log rows (`document_access_log`) are never deleted when
  their document is purged** — the audit trail is intentionally not
  foreign-keyed to the document it describes, so a record of who read (or
  was denied) a document survives that document's deletion. Audit rows are
  not kept forever either, though: an audit trail that never ages out is
  its own privacy problem, so they are purged independently, on the same
  90-day window below, once they are old enough that they no longer serve
  an active investigation.

Three further stores hold verbatim, potentially sensitive user text, all
subject to that same documented 90-day window:

- `./data/app/hallucinations.jsonl` — the question and rejected-claim text
  behind every citation the validator refused to render.
- `./data/app/classifier_failures.jsonl` — the question behind every crisis-
  classifier call that could not complete.
- the `hallucination_log` database table (unused today, per above — but
  purged regardless, so the mechanism is already correct on the day it
  isn't).

Task 7's fix mounted `./data/app` as a host volume specifically so the two
JSONL logs survive a container recreate. **That also means they are now
permanent unless something purges them** — nothing in `docker compose up -d`
does this automatically, by design: a retention purge deletes data, so this
project's default is opt-in-by-schedule, not opt-out. Run it yourself:

    docker compose run --rm purge

(a `profiles: ["tools"]` service in `docker-compose.yml`, wired to
`scripts/purge_retention.py`; deliberately excluded from the default
`docker compose up -d` service set — see that file's comment on the `purge`
service). Outside Compose:

    python scripts/purge_retention.py

One run purges, in a single database transaction, the `hallucination_log`
table, expired anonymous `documents`, expired anonymous `conversations`
(with their messages and documents), and stale `document_access_log` rows —
plus the two JSONL logs — and prints one `[purge] ...: removed N row(s)`
line per store, so a cron log always shows exactly what happened, never
nothing. Pass `--dry-run` to preview the same counts for the database stores
without deleting anything (the JSONL logs are reported as skipped in
dry-run, since previewing them would need a second copy of the file-reading
logic that could drift from the real one):

    python scripts/purge_retention.py --dry-run

**Schedule it — neither command above runs on its own.** A host cron entry
(adjust the path):

    # /etc/cron.d/openlaw-purge -- daily at 03:15
    15 3 * * * root cd /path/to/openlaw-in && docker compose run --rm purge >> /var/log/openlaw-purge.log 2>&1

or the systemd-timer equivalent. The command is idempotent and safe to run
more often than the window requires, including overlapping itself — an
empty or not-yet-stale store is a normal no-op (exit 0), not a failure; a
second run back-to-back finds nothing newly stale and reports zeroes rather
than erroring or double-counting. It only exits non-zero if a purge itself
raises (e.g. the database is unreachable), which a cron log should surface,
not hide — and in that case nothing from that run is committed.

### Reserved, currently-unused tables

`alembic upgrade head` creates the application tables, including `users`,
`quota_usage`, `conversations`, `messages`, `documents`, `document_excerpts`,
`document_access_log`, and `hallucination_log`. All but `hallucination_log`
are written by shipped code today (conversations, messages, and documents
since the persistence work that followed this schema; users and quota_usage
since authentication and quota tracking). **`hallucination_log` alone
remains reserved for future use** — nothing in `src/` or `scripts/` writes
to it; only `tests/db/` constructs it, to prove the schema itself is sound.
It stays in the migration (rather than being dropped) because the schema is
intentional forward design, not accidental debt, and its access precondition
above is written down precisely so a future writer sets up the restricted
role first, not after.

## Uploaded documents

A person can attach their own paperwork — a lease, an eviction notice, an FIR
copy, a medical report — and the answer may quote it. Everything an operator
needs to know before enabling that is here, including the parts that are not
flattering.

### What is accepted, and what is refused

| Limit | Value | Setting |
|---|---|---|
| Content types | `application/pdf`, `text/plain`, `.docx` | `openlaw.documents.ALLOWED_CONTENT_TYPES` |
| Maximum upload size | 10 MiB | `OPENLAW_MAX_UPLOAD_BYTES` |
| Maximum pages per document | 20 | `OPENLAW_MAX_DOCUMENT_PAGES` |
| Documents per conversation | 3 | `OPENLAW_MAX_DOCUMENTS_PER_CONVERSATION` |
| Quota cost | `max(1, ceil(pages / 5))` units | `openlaw.documents.PAGES_PER_QUOTA_UNIT` |
| Excerpts reaching the model | at most 6, 1,200 chars each, 12,000 total | `openlaw.documents.context` |

**There is no OCR.** An image is not an accepted content type, and a PDF with
no extractable text layer is refused with `no_text`. This means the most
vulnerable upload is the most likely to be refused: someone photographing an
FIR on a cheap phone gets a refusal, not an answer. That is the deliberate
choice — a refusal that routes to DLSA/NALSA is honest, where silently
reading a photograph as an empty document and answering anyway would not be
— but it is a real coverage gap, not a detail. Adding OCR is a project of its
own.

**A plain-text file over about 60 KB is refused, and this is documented rather
than fixed.** Plain text is paginated at 3,000 characters per page
(`openlaw.documents.CHARS_PER_TEXT_PAGE`), so 60,000 characters is 20 pages
and anything past that crosses the 20-page cap. Raising the text page size
was considered and rejected: `max_document_pages` is simultaneously the page
cap, the basis of the `ceil(pages / 5)` quota charge, and the unit a reader
sees in a document citation, so making a text "page" six or ten times larger
than a PDF page would make the same quota charge mean different amounts of
work and the same page number mean different amounts of text. The cap is
honest as it stands; a 60 KB `.txt` legal document is also well outside what
this feature is for. Operators who genuinely need more can raise
`OPENLAW_MAX_DOCUMENT_PAGES`, at a proportional cost in quota and context.

### Encryption at rest, and what rotating the key destroys

Every stored byte of document content and every excerpt is AES-256-GCM
ciphertext (`openlaw.documents.crypto`), with a fresh 96-bit nonce per row
and the row's own id bound in as associated data, so a ciphertext moved to
another row fails authentication instead of decrypting as somebody else's
document. `OPENLAW_DOCUMENT_ENCRYPTION_KEY` is a base64-encoded 32-byte
CSPRNG value — **the configured value IS the key**; there is no derivation
step, so a password or a phrase there is a weak key, not a seed.

**Changing the key without `OPENLAW_DOCUMENT_ENCRYPTION_KEY_PREVIOUS`
destroys every existing upload.** Rows record a non-secret fingerprint of
the key that encrypted them (`key_id`), and decryption looks the key up by
that fingerprint; a row whose key is in neither slot cannot be read by
anybody, including the person who uploaded it. Setting the retiring key as
`..._PREVIOUS` keeps those rows readable while new uploads move to the
current key — that is a one-generation grace window, not key rotation.
**There is no re-encryption job**, no per-tenant envelope, and no key
management beyond these two environment variables. That is acceptable for a
self-hosted single-tenant deployment and is **not** acceptable for a managed
multi-tenant one; draw that line before crossing it.

**Uploads are OFF by default for self-hosters, and you turn them on
deliberately.** `OPENLAW_UPLOADS_ENABLED` defaults to `false` in both
`docker-compose.yml` and the shipped `.env.example` (kept in lockstep by
`tests/test_config.py::test_env_example_agrees_with_compose_self_host_defaults`),
because the alternative was worse: `Settings.uploads_enabled` defaults to
`True` in code, and before this `docker-compose.yml` passed neither that switch
nor `OPENLAW_DOCUMENT_ENCRYPTION_KEY` to the `api` container at all — so a
compose self-host advertised uploads, ignored the key the operator had
configured in `.env`, and failed on the first real upload. To enable them:

    python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
    # put that in OPENLAW_DOCUMENT_ENCRYPTION_KEY in .env, then:
    #   OPENLAW_UPLOADS_ENABLED=true
    docker compose up -d
    ./scripts/verify_selfhost.sh   # checks the key's presence and length, never prints it

`OPENLAW_UPLOADS_ENABLED=false` stops uploads and deliberately does **not**
stop deletions — turning the feature off must never trap somebody's FIR in
storage they can no longer clear.

### Anonymous uploads expire in 24 hours, on purpose

An anonymous upload and the conversation holding it get
`now + OPENLAW_ANONYMOUS_UPLOAD_TTL_HOURS` (default 24) at write time; an
authenticated one gets `NULL` and is kept until the owner deletes it. See
"Retention" above for the purge mechanism and how to schedule it.

The asymmetry is the point. Anonymous ownership is **a cookie**: there is no
way to contact that person later, no way for them to prove the upload was
theirs after they clear their browser, and therefore no way for them to come
back and delete it. Keeping an anonymous FIR or medical report indefinitely
would create an undeletable document belonging to nobody — the single worst
outcome available here — so its lifetime is bounded by a timer instead of
being left to someone who may never return. The residual exposure is real
and is not hidden by the TTL: clearing cookies orphans a document until it
expires, and a shared cybercafe machine *within* that 24-hour window shares
the document with the next person at that machine. The invitation to sign in
is the only mitigation available without an identity.

### Cost: a document turn is two model calls, and only one of them caches

**Budget two full uncached model calls for every turn that carries a
document.** A document turn runs two separate generation passes — one that
reads the uploaded text, one that composes legal claims — and the
document-analysis pass's prefix is below the provider's minimum cacheable
length, so it pays full input price every time. The pass separation is a
safety property, not an inefficiency to optimise away: it is what stops text
inside somebody's lease from steering a claim about the law.

The legal pass does cache, and the saving is large and measured, not assumed:
on a second identical call, **3,520 of 3,622 prompt tokens came back from
cache (~97%), billed at 0.2x with no write cost** — confirmed from live
responses and from first-party cost data, read back from
`usage.prompt_tokens_details.cached_tokens`. Caching on that route is
automatic: there is no breakpoint to declare, so it is earned purely by
keeping the prefix byte-stable (`openlaw.answers.prompts.build_turn_messages`)
and forfeited by putting variable bytes first. Prefixes under roughly
1,000–1,500 tokens were never cached at all in measurement, which is exactly
why the document pass does not benefit. Earlier versions of this repository
asserted the opposite — that OpenRouter caching "does not transfer" — and that
claim has been removed everywhere rather than softened.

### Safety limits that documents do not close

Stated here because a reader who takes the citation guarantee at face value
would otherwise over-trust these paths.

- **`Claim.text` is never entailment-checked.** The validator
  (`openlaw.answers.validate`) proves that every id a claim cites was in the
  set shown to the model that turn, and that a legal claim never cites a
  `doc:` excerpt id nor a document claim a corpus chunk id. It does **not**
  prove the claim's sentence is what the cited chunk or excerpt actually
  says. There is no entailment check anywhere in this pipeline, in either
  namespace. Pass separation stops uploaded text from steering a legal claim;
  nothing verifies a claim against its source.
- **Hindi safety text is implemented but unverified.** Every static Hindi
  string on the crisis, refusal and footer paths, the 21 Devanagari crisis
  patterns, and roughly ten new interface strings are machine-written and
  have had **no native-speaker review**. `python scripts/review_translations.py`
  prints the whole backlog as a review sheet; it is not validated until a
  native speaker has signed off on that sheet. Treat Hindi crisis routing as
  unverified, not as tested.
- **A Devanagari document contributes no excerpt to the answer at all.** Found
  by the adversarial gate while writing it, and not yet fixed.
  `openlaw.index.sparse.tokenise` matches `[a-z0-9]+`, so a document written in
  Devanagari yields no tokens beyond its digits, shares none with a Devanagari
  question, and `select_excerpts` returns nothing — the turn proceeds as if no
  file had been attached, with no refusal shown. Upload-time crisis scanning is
  unaffected, because it reads the whole document rather than selected
  excerpts. It is not fixed here because `tokenise` is shared with the BM25
  sparse vectors already built into Qdrant
  (`scripts/build_sparse_vectors.py`): widening its pattern would make
  query-time tokenisation disagree with the built index, which is a measured
  retrieval change and a task of its own. Pinned by
  `tests/redteam/test_document_injection.py::test_a_devanagari_document_contributes_no_excerpt`,
  so a future fix cannot land without this paragraph being revisited.
- **The Devanagari crisis patterns favour precision over recall**, so a
  Devanagari document can still miss. They require complete, unambiguous
  phrases rather than single evocative words, because a document-only match
  adds a helpline card beside an ordinary legal answer and a card that
  appears on every Hindi document is one people learn to ignore. The regex
  layer remains script-limited — every Indic script other than Devanagari is
  uncovered — which is why `openlaw.api.refusal`'s script hedge still
  matters.
- **An upload refusal carries NALSA only**, not a category helpline. There is
  no question text on an upload request to classify, so the refusal cannot
  know whether the person is in a domestic-violence situation or disputing a
  deposit, and it offers the general free-legal-aid route rather than guessing
  a category. Relatedly, and pre-existing: a 422 on a malformed `/ask` body
  never reaches the crisis regex, so a crisis question rejected by schema
  validation gets the generic refusal.
- **Per-process state does not survive horizontal scaling.** Conversations and
  documents are in Postgres, but the anonymous quota and the rate limiter are
  still per-process. Unchanged by uploads; restated because uploads raise the
  value of a bypass.

### The adversarial gate

`tests/redteam/` is a CI gate, not a one-off exercise:
`test_document_injection.py` drives every hostile body in
`tests/redteam/prompts/injection.txt` through the real validator, the real
fence and the real HTTP surface with a model stub that **obeys** the
injection, and `test_cross_account.py` drives two token subjects across every
document and conversation endpoint. The assertions are structural — no legal
claim cites a `doc:` id, no uploaded text becomes a source card, the
escalation footer is on every response, a cross-owner read is a 404 — never
anything about model wording.

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

`alembic upgrade head` (above) creates the eight application tables
(`users`, `quota_usage`, `conversations`, `messages`, `documents`,
`document_excerpts`, `document_access_log`, `hallucination_log`) in the
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
    python scripts/restore_acts_india.py       # 1,098,269 provisions (~11.2 GB) -> acts_india
    python scripts/build_sc_index.py           # 371,247 Supreme Court chunks -> sc_judgments_v1
    python scripts/build_hc_index.py           # ~1.85M Punjab & Haryana HC chunks (default --court)
    python scripts/build_hc_index.py --court delhi  # ~1.06M Delhi HC chunks, same shared collection
    python scripts/build_sparse_vectors.py     # REQUIRED -- see below
    python scripts/validate_corpus.py          # the gate: all citations resolve

**`build_sparse_vectors.py` is not optional (Task 7 review, C3).** Retrieval
and `/readyz` both require `acts_india_v1`, not `acts_india` -- see
`openlaw.retrieval.search.SEARCH_COLLECTIONS`. `restore_acts_india.py` only
ever populates `acts_india` (the untouched publisher snapshot); it is
`build_sparse_vectors.py` that copies `acts_india`'s citable points into the
new `acts_india_v1` collection (dropping the ones with no resolvable
government URL) and writes consistent BM25 sparse vectors into both
`acts_india_v1` and `sc_judgments_v1`. Skip it and every one of the steps
above can succeed while `/readyz` stays `degraded` forever and statute
retrieval returns nothing -- run it after `build_sc_index.py`/`build_hc_index.py`
and before `validate_corpus.py`, as ordered above.

`build_hc_index.py` streams and checkpoints per court (`data/hc_index_checkpoint_<court>.json`,
git-ignored) so an interrupted multi-hour run resumes with `python scripts/build_hc_index.py`
(no flag needed) rather than re-embedding from the start; `--restart` discards the
checkpoint, and `--limit N` stops after N chunks to prove the pipeline on a
bounded slice.

**Optional -- the knowledge graph.** Graph expansion is OFF by default
(`OPENLAW_GRAPH_EXPANSION_ENABLED=false`, see `.env.example`) because live
measurement showed it mostly adds filler at today's scale (see
`openlaw.retrieval.rerank`'s module docstring), and neither `/readyz` nor
ordinary Q&A requires it. Build it only if you intend to turn that setting
on, or want the PageRank/Louvain authority signals in Neo4j:

    python scripts/build_graph.py              # ~1.3M nodes, ~1.9M edges, resumable
    python scripts/run_gds.py                  # PageRank + Louvain, run once per corpus release

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
   query embedder is the local, open-weight `voyage-4-nano`, whose CODE is
   baked into the `api` image but whose WEIGHTS download from Hugging Face
   the first time the container starts -- see step 4). `docker-compose.yml`
   fails immediately with a named, readable error if
   `OPENLAW_ANTHROPIC_API_KEY` is missing -- never a stack trace, never a
   service that starts and only fails on a real user's question.

3. **Build the corpus** (separately from `docker compose up` -- see above;
   this needs the Python environment on your host, not a container, since
   these scripts read the raw HuggingFace dataset snapshots directly). The
   two raw datasets (`OPENLAW_VAQUILL_ROOT`, `OPENLAW_KANOONGPT_ROOT`) are
   NOT bundled anywhere -- see `.env.example` for exactly how to download
   them, their licences, and their real disk cost before you start:

       pip install -e ".[dev,local-nano]"
       python scripts/make_fixtures.py
       python scripts/restore_acts_india.py       # ~11.2 GB, tens of minutes
       python scripts/build_sc_index.py           # needs OPENLAW_VOYAGE_API_KEY
       python scripts/build_hc_index.py            # needs OPENLAW_VOYAGE_API_KEY, hours
       python scripts/build_hc_index.py --court delhi
       python scripts/build_sparse_vectors.py      # REQUIRED -- builds acts_india_v1; see "Corpus build" above
       python scripts/validate_corpus.py           # the gate: all citations resolve

   You can skip straight to step 4 and come back to this once the
   application is confirmed up -- the two do not depend on each other.

4. **Bring up the stack.**

       docker compose up -d

   This starts Postgres, Qdrant, and Neo4j; runs `alembic upgrade head`
   against Postgres via the one-shot `migrate` service (the `api` container
   will not start until migrations succeed -- see "Database migrations"
   above for the `alembic stamp head` escape hatch if these tables already
   exist some other way, and `docker compose run --rm migrate alembic
   stamp head` to run that same escape hatch inside a container rather than
   needing `alembic` installed on your host); and starts `api`, `web`, and
   `proxy` -- the single reverse-proxy ingress this stack listens on (see
   "Reverse-proxy trust" below for why it exists).

   **First start downloads the nano model weights.** `api` builds its query
   embedder before it starts serving anything, which means the FIRST
   container start (and any subsequent start where the `hf_cache` volume
   was removed) blocks for as long as that download takes -- a few minutes
   on an ordinary connection, longer on a slow one. This is why step 5's
   verification allows a generous wait; if `docker compose logs api` shows
   it still downloading, that is expected, not a hang.

5. **Verify it.**

       ./scripts/verify_selfhost.sh

   Expected: `[Selfhost] PASS`. This confirms the stack is up, every
   datastore is reachable, and the API and frontend are both serving. When
   `OPENLAW_UPLOADS_ENABLED=true` it also verifies the upload surface: that
   `OPENLAW_DOCUMENT_ENCRYPTION_KEY` is present and decodes to 32 bytes
   (**the key is never printed** -- only its presence and length), and that a
   tiny text file can be uploaded, deleted, and is then gone. With uploads off
   those checks are skipped and said to be skipped. It does **not** require the
   corpus build from step 3 to be finished first (see that script's own comments for exactly what it does and does
   not check, and why). Re-run it after the corpus build finishes: with
   `/readyz` reporting ready, its final check asks a real question and
   checks BOTH `is_answerable: true` and a non-empty `sources` list before
   claiming a cited answer came back -- `corpus_version` alone (present on
   every response, cited or not) is not treated as proof of anything. If
   that specific question has no good match yet, the script still prints
   PASS for the stack itself but says plainly that it did not verify a
   citation, rather than claiming one it never checked.

6. Open **http://localhost:8080** and ask a question. (Not `:3000` or
   `:8000` -- those are loopback-only now; see "Datastore and application
   ports" below.)

### Quota is disabled by default for self-hosters

`OPENLAW_QUOTA_ENABLED` defaults to `false` in both `docker-compose.yml`
and the shipped `.env.example` (kept in lockstep by
`tests/test_config.py::test_env_example_agrees_with_compose_self_host_defaults`
-- Task 7 review, C2: an earlier version of `.env.example` shipped `true`
here, which `cp .env.example .env` then silently used to override
`docker-compose.yml`'s own `false` default, turning the hosted tier's rate
limit ON for every self-hoster who followed this exact README). A legal-aid
body running its own instance should not inherit the hosted tier's
anonymous-3/signed-in-30-per-month limit. Set it to `true` (and, once you
are behind HTTPS, set `OPENLAW_COOKIE_SECURE=true` too -- see the next
section) if you specifically want that limit on your own deployment.

**This is legal information, not legal advice.** Every answer carries this
notice and routes to DLSA/NALSA for anything beyond what a citation can
responsibly settle -- self-hosting this does not change that; see
"Coverage limits" above for exactly how far the underlying corpus goes.

### Reverse-proxy trust, and what changes if you add your own

**A Next.js `rewrites()` entry is not a reverse proxy, and this stack does
not trust it as one (Task 7 review, H1).** `web` proxies `/ask` to `api`
server-side purely so the anonymous-quota cookie round-trips with zero CORS
configuration -- but Next.js's rewrite never sets `X-Forwarded-For` on its
own, and copies whatever a caller sent through unchanged. Trusting `web`'s
address without something upstream of it actually setting that header
honestly would be *worse* than trusting nothing: every real visitor
collapses into one shared 30-requests-per-minute bucket, and a client can
forge its own `X-Forwarded-For` on a same-origin `fetch()` to bypass the
limiter outright, on a service that spends real money per question.

That is why `docker-compose.yml` ships a real reverse proxy, `proxy`
(nginx, `nginx/nginx.conf`), as the ONLY externally-reachable service --
`api` and `web` are bound to `127.0.0.1` only (see below). `proxy`
overwrites `X-Forwarded-For` with the connection it actually observed
(`proxy_set_header X-Forwarded-For $remote_addr;` -- never
`$proxy_add_x_forwarded_for`, which would append to a forged value instead
of replacing it); `web`'s rewrite then forwards that genuine value through
to `api` unchanged. `OPENLAW_TRUSTED_PROXY_IPS` is set to `web`'s own fixed
address on the Compose network (`172.31.238.10`, via the custom `ipam`
block) -- not `proxy`'s -- because `web` is still the one address that
directly connects to `api`; adding `proxy` did not change that, it only
made what arrives in `X-Forwarded-For` honest.

If you replace `proxy` with your own ingress (a managed load balancer, a
different reverse proxy, TLS termination for a public deployment) and it
still forwards to `web`, `OPENLAW_TRUSTED_PROXY_IPS` needs **no change** --
**provided your replacement also overwrites `X-Forwarded-For` the way
`nginx/nginx.conf` does.** Verify that explicitly; do not assume it. The
one case that DOES need a change is an ingress that talks to `api` directly
(bypassing `web`'s rewrite), or a future multi-replica `api` behind a load
balancer -- set `OPENLAW_TRUSTED_PROXY_IPS` to that proxy's/load balancer's
actual address instead.

### Datastore and application ports never leave the host

Qdrant, Neo4j, Postgres, `api`, and `web` are ALL bound to `127.0.0.1` in
`docker-compose.yml` (Task 7 review, H3: an earlier version published
`api`/`web` on every interface, exposing an unauthenticated,
Anthropic-money-spending `/ask` to the entire local network with no TLS).
Datastore loopback binding is a hard project constraint, not a default to
reconsider. `proxy` (port 8080) is the only service meant to be reached
from outside this host -- see "Reverse-proxy trust" above. Postgres/Neo4j's
default development credentials (`openlaw`/`openlaw`, `neo4j`/`openlawdev`)
are fine behind loopback binding; override them via
`POSTGRES_PASSWORD`/`NEO4J_PASSWORD` in your `.env`, and put TLS on `proxy`
(or replace it), before exposing this stack beyond your own machine.
