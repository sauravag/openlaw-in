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

**This is legal information, not legal advice.** Always verify against the official
source before relying on anything here.

## Setup

    cp .env.example .env
    pip install -e ".[dev,local-nano]"
    docker compose up -d
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

## Corpus build

    python scripts/make_fixtures.py            # test fixtures (once)
    python scripts/restore_acts_india.py       # 1,098,269 provisions (~11.2 GB)
    python scripts/build_sc_index.py           # 371,247 Supreme Court chunks
    python scripts/validate_corpus.py          # the gate: all citations resolve
