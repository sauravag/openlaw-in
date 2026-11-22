---
pretty_name: OpenLaw.in Corpus (v2026.08)
language:
  - en
license: other
license_name: cc-by-4.0-and-apache-2.0
license_link: https://creativecommons.org/licenses/by/4.0/
task_categories:
  - question-answering
  - text-retrieval
tags:
  - legal
  - india
  - rag
  - embeddings
  - qdrant
size_categories:
  - 1M<n<10M
---

# OpenLaw.in Corpus — v2026.08

> **Draft dataset card.** This is the text to publish as the README of the
> Hugging Face dataset repo that `scripts/fetch_corpus.py` downloads from.
> Replace `<your-org>/<your-repo>` with the real repo id. There is no default
> id in the code: operators must pass `--repo-id` / `OPENLAW_CORPUS_REPO_ID`
> and a pinned `--revision`, and publishers must commit each manifest's
> SHA-256 (printed by `scripts/export_corpus.py`) to `corpus.lock.json`.
> Nothing in this repository uploads anything — publishing needs the
> publisher's own Hugging Face credentials and is their decision.

Embedded, retrieval-ready Indian primary law: **13,651,969 chunks** of
legislation, regulator instruments, Supreme Court judgments and six High
Courts' judgments, with dense and sparse vectors, published as zstd parquet
shards. It exists so that running OpenLaw.in — a free, open-source legal-aid
Q&A system for ordinary Indian citizens — does not require ~30 days of local
compute and a paid embedding bill.

---

## ⚠️ Read this before you query these vectors

**EMBEDDER COMPATIBILITY IS NOT OPTIONAL.**

The dense vectors were produced by **voyage-4-nano at 1024 dimensions**,
Matryoshka-truncated from the model's full width and then **renormalised to
unit length**. Any query you search them with MUST be embedded by that same
model, at those same 1024 dimensions, truncated and renormalised the same way.

**Pairing them with a different embedder DOES NOT RAISE AN ERROR.** Vector
search always returns its nearest neighbours, so a mismatched embedder
returns confidently-ranked near-garbage, silently, for every query. This is
measured, not theoretical: voyage-4-nano queries against documents embedded
by the Voyage API's `voyage-4-lite` scored **15.0% top-1** and **31.7%
top-10** accuracy, median rank **61 of 300** — because nano's and lite's
independent approximation errors against full `voyage-4` do not cancel
against each other.

A legal-aid answer built on that retrieval **cites the wrong law** while
looking exactly as confident as a correct one. If you are not using
voyage-4-nano at 1024 dimensions, these vectors are not usable for you —
re-embed the `text` payload field with your own model instead.

The OpenLaw.in default (`OPENLAW_QUERY_EMBEDDER_BACKEND=local_nano`) is the
matching embedder, is open-weight, and needs no API key.

---

## Provenance and licences

This dataset is a **derivative** of two upstream open datasets. It is not
original work, and **attribution is a licence condition, not a courtesy** —
it is imposed by upstream eCourts, NCLT and NCLAT policy.

| Upstream dataset | What it contributed | Licence |
|---|---|---|
| [**vaquill/open-india-law**](https://huggingface.co/datasets/vaquill/open-india-law) | The primary law itself: judgment and legislation text, chunked, with `court_code`, `case_id` (the CNR) and `source_pdf_s3_url` | **CC BY 4.0** |
| [**KanoonGPT/indian-case-laws**](https://huggingface.co/datasets/KanoonGPT/indian-case-laws) | Structured case-law metadata, used to resolve every chunk to a government source URL | **Apache-2.0** |

### Attribution you must carry

If you use, redistribute or build on this dataset, carry all three notices:

- **vaquill/open-india-law**, licensed **CC BY 4.0** — credit the creator,
  link the licence, and **state that changes were made** (they were: see
  "What changed from upstream"). <https://creativecommons.org/licenses/by/4.0/>
- **KanoonGPT/indian-case-laws**, licensed **Apache-2.0** — retain the
  copyright notice and licence text, and state changes.
- **This dataset**, and the code that produced it, **Apache-2.0**.

The underlying legal text is a **Government work** under s.17(d) of the
Indian Copyright Act 1957 and is reproducible under s.52(1)(q). That is what
makes publishing it lawful; it does not relieve you of the upstream
attribution above.

### What changed from upstream

Stated plainly, because CC BY 4.0 requires it:

1. Chunks were filtered to **English only** and deduplicated.
2. Every chunk was joined to KanoonGPT metadata to resolve a **government
   `source_url`**. For the judgment collections, chunks whose citation could
   not be resolved were **dropped before indexing**, not published.
3. Chunks were **embedded** (see below) — the vectors are new work.
4. **BM25 sparse vectors were rebuilt** under one consistent scheme. The
   upstream sparse vectors could not be used as they stood: a 5,000-point
   sample found two incompatible weighting schemes mixed together (51.7%
   BM25-saturated, 47.6% raw term counts), which makes hybrid search score a
   raw-count chunk roughly 7× higher than a comparable BM25 chunk for
   reasons unrelated to relevance.
5. For statutes, **137,441 provisions (12.5%) with no resolvable government
   URL were excluded** from the published collection. Every one is a
   regulator instrument (`jurisdiction: "regulatory"`) — MCA notifications,
   Law Commission reports and similar — published upstream without a stable
   government URL. Nothing was deleted from anyone's data; those provisions
   are simply not in this retrieval collection, because a chunk that cannot
   be cited must never be shown to a citizen as a source.

---

## How the embeddings were produced

**Dense.** `voyage-4-nano`, 1024 dimensions, Matryoshka-truncated from the
model's full width and renormalised to unit length, cosine distance. The
judgment collections were embedded **locally** with the open-weight nano
model, deliberately *not* with the Voyage API's `voyage-4-lite` — see the
warning above for the 15%-accuracy measurement that forced that choice.

Published as **float16**, which halves the download. This is safe here and
was verified rather than assumed: over 6,000 random points drawn from all
three collections, the worst float32 → float16 → float32 round-trip cosine
similarity was **0.9999995**, and the worst change to any query–document
score was **1.2 × 10⁻⁴** — orders of magnitude below the gap between
adjacent search results, with top-1 and top-10 rankings unchanged on the
sampled pools. The vectors are unit-norm with no component above ~0.15, so
every value sits well inside float16's normal range.
`scripts/export_corpus.py --verify-fp16` re-runs this measurement and refuses
to export if it ever stops holding.

**Sparse.** BM25, storing only the **term-frequency** component (k1 = 1.2,
b = 0.75). Inverse document frequency is applied by Qdrant at query time via
the collection's `modifier: "idf"`, derived from real collection statistics
so it stays correct as the collection grows. Tokens are lowercase
alphanumeric runs (`[a-z0-9]+`) hashed to 32-bit slots with blake2b — digits
stay attached to letters so legal terms of art like `498A` and `377` survive
tokenisation, which is how citizens and lawyers alike refer to them.

**If you restore this into Qdrant yourself, you MUST set `modifier: "idf"`
on the sparse vector.** Without it Qdrant never supplies the IDF and hybrid
search scores wrongly — silently, with no error anywhere.
`scripts/fetch_corpus.py` does this for you.

---

## Contents, sizes and point counts

| Slice | Where its law binds | Points | Download | Qdrant on disk |
|---|---|---|---|---|
| `acts_india_v1` | All of India | 961,136 | 2.6 GiB | 7.8 GB |
| `sc_judgments_v1` | All of India | 371,247 | 1.6 GiB | 4.6 GB |
| `hc_judgments_v1/punjab-and-haryana` | Punjab, Haryana, Chandigarh UT | 1,845,344 | 5.5 GiB | ~17 GB |
| `hc_judgments_v1/delhi` | NCT of Delhi | 1,053,138 | 3.9 GiB | ~10 GB |
| `hc_judgments_v1/bombay` | Maharashtra, Goa, Dadra & Nagar Haveli, Daman & Diu | 2,788,379 | 9.4 GiB | ~26 GB |
| `hc_judgments_v1/karnataka` | Karnataka | 1,467,133 | 5.5 GiB | ~14 GB |
| `hc_judgments_v1/madras` | Tamil Nadu, Puducherry | 2,610,454 | 9.1 GiB | ~24 GB |
| `hc_judgments_v1/patna` | Bihar | 2,555,138 | 8.2 GiB | ~24 GB |
| **Total** | | **13,651,969** | **45 GiB** | **~127 GB** |

Download sizes are extrapolated from a measured 3,000-point sample of each
slice; the Qdrant figures are what each collection actually occupies once
restored, and are what you should provision disk against.

**Take only what you need.** Statutes and Supreme Court judgments bind
everywhere in India and are the useful minimum (**4.2 GiB**). A High Court's
judgments bind only within its own jurisdiction and are merely persuasive
elsewhere, so add the one court you are in. Nobody building a tenant-rights
tool in Bengaluru needs 2.8M Bombay judgments.

### Files

```
<slice>/manifest.json            # collection, court_code, points, shard list, dtype
<slice>/shard-00000.parquet      # ~25,000 rows each, zstd
<slice>/shard-00001.parquet
...
```

### Parquet schema

| Column | Type | Meaning |
|---|---|---|
| `chunk_id` | `string` | Stable chunk identity. The Qdrant point ID is a deterministic UUID5 of this, so restoring twice updates points rather than duplicating them. |
| `dense` | `fixed_size_list<float16>[1024]` | The voyage-4-nano vector. Cast to float32 before use. |
| `sparse_indices` | `list<uint32>` | BM25 term slots, ascending. |
| `sparse_values` | `list<float32>` | BM25 term-frequency weights. IDF is **not** included — see above. |
| `payload` | `string` (JSON) | The full Qdrant payload, losslessly. |

The payload is one JSON column rather than typed columns because
`acts_india_v1` carries the upstream statute schema while the judgment
collections carry OpenLaw's own; a JSON column is lossless for both and
cannot silently drop a field. Judgment payloads carry `chunk_id`, `case_id`,
`join_key`, `chunk_index`, `text`, `text_original`, `court_code`, `judges`,
`decision_date` (ISO-8601, e.g. `2019-04-11`), `language_code`,
`corpus_version`, `source_url` and `headnote`.

---

## How to use it

```bash
pip install -e ".[dev,local-nano,corpus]"     # in the OpenLaw.in repo
docker compose up -d qdrant
python scripts/fetch_corpus.py --repo-id <your-org>/<your-repo> \
    --revision <commit-or-tag> --collections acts_india_v1,sc_judgments_v1
python scripts/fetch_corpus.py --repo-id <your-org>/<your-repo> \
    --revision <commit-or-tag> --courts karnataka
```

### Integrity: shards are verified, and you must pin a revision

**An unverified corpus can forge both the law and its citation.** Whoever
serves the shards controls the statute text and the `source_url` shown beside
it; the answer pipeline only proves a chunk was retrieved, not that its text
is genuine. So each target's `manifest.json` records a **SHA-256 per shard**,
`fetch_corpus.py` verifies every shard against it, and the manifest's own
SHA-256 is pinned in `corpus.lock.json` in the OpenLaw.in git repository, so
the chain ends in git rather than in the download. Pin `--revision` to a
commit hash or release tag (branch names are refused); a missing pin, an
unlisted shard or any hash mismatch stops the run before anything is written
to Qdrant. **On a mismatch:** do not ingest the download. Delete
`data/corpus_download`, confirm the repo id and revision are the intended
ones, re-run, and report it to the maintainers if it persists.

`fetch_corpus.py` creates each collection with the correct geometry (1024-dim
cosine, on-disk, int8-quantised, **`idf` on sparse**), upserts in batches,
checkpoints after every batch so an interruption resumes with the same
command, and **refuses to overwrite a collection that already holds points**.

To read the shards without Qdrant:

```python
import json

import numpy as np
import pyarrow.parquet as pq

table = pq.read_table("sc_judgments_v1/shard-00000.parquet")
row = table.to_pylist()[0]
dense = np.asarray(row["dense"], dtype=np.float32)   # 1024-dim, unit norm
payload = json.loads(row["payload"])
print(payload["source_url"], payload["text"][:200])
```

---

## Known limits

Stated plainly, because a legal dataset that overstates its coverage is
dangerous.

### Coverage

- **Snapshot `v2026.08` — an archive, not current law.** It knows nothing of
  any amendment, repeal or judgment after that snapshot. Always verify
  against the official source before relying on anything.
- **English only.** Non-English chunks were filtered out at build time.
- **4 of 25 High Courts.** The other 21 are registered in the application
  (so they are *queryable*) but hold **no points** and return nothing.
  Kerala, Telangana, Rajasthan, Madras, Patna, Allahabad and the rest are
  not here.
- **No District or trial court decisions**, and **no consumer fora** (NCDRC
  and the State Commissions).
- **Retrieval coverage is not corpus coverage.** 1,786,868 more judgments
  exist as extracted text upstream than are embedded here.
- **12.5% of upstream statute provisions are absent** — the 137,441
  regulator instruments with no resolvable government URL (see "What changed
  from upstream").

### Correctness and safety

- **A resolvable citation is not an entailment check.** OpenLaw.in's
  validator proves that every chunk a claim cites was actually retrieved and
  shown to the model on that turn, and that it carries a resolvable
  government `source_url`. It does **not** check that the claim's wording is
  what that chunk says. So an *unsupported citation* is structurally
  impossible, while **a claim that misstates a provision it correctly cites
  is not** — `Claim.text` is never entailment-checked, anywhere in the
  pipeline, in either the corpus or the uploaded-document namespace. This is
  why every answer must be shown with its source card: the citation is there
  to be read, not merely counted. If you build on this dataset, do not
  present generated text as verified law.
- **Devanagari text is effectively invisible to the sparse vectors.**
  Tokenisation matches `[a-z0-9]+`, so Devanagari yields no tokens beyond
  its digits. In OpenLaw.in this has a measured consequence: an uploaded
  Devanagari *document* contributes no excerpt to the answer at all, and the
  turn proceeds as if no file had been attached, with no refusal shown. The
  pattern is deliberately not widened, because it is shared with the BM25
  vectors already built into these shards — widening it would make
  query-time tokenisation disagree with the published index, which is a
  measured retrieval change and a task of its own. Every Indic script other
  than Devanagari is wholly unhandled by that layer. If your use case
  involves non-Latin scripts, rely on the dense vectors and treat the sparse
  half as absent.
- **Hindi strings in the application have had no native-speaker review**;
  treat Hindi crisis routing as unverified rather than tested.
- **The 12.5% statute gap means a consumer must filter on `source_url`.**
  Any system built on `acts_india_v1` should exclude chunks without a
  resolvable government URL before showing or citing them. OpenLaw.in does
  this; a naive consumer of these shards would not.

### Not published here

`acts_india` — the upstream publisher's own 1,098,577-point Qdrant snapshot,
carrying **their** vectors — is deliberately **not** redistributed. Only
`acts_india_v1`, this project's citable-only derivative with rebuilt sparse
vectors, is. Restore the original from the publisher's own mirror if you
want it.

---

## Citation

```bibtex
@misc{openlaw_in_corpus_2026,
  title        = {OpenLaw.in Corpus v2026.08},
  author       = {Agarwal, Saurav},
  year         = {2026},
  howpublished = {Hugging Face dataset},
  note         = {Derived from vaquill/open-india-law (CC BY 4.0) and
                  KanoonGPT/indian-case-laws (Apache-2.0). Embedded with
                  voyage-4-nano at 1024 dimensions.}
}
```

**This is legal information, not legal advice.** Always verify against the
official source before relying on anything built from this dataset.
