# OpenLaw.in — Design Specification

**Date:** 2026-09-08
**Status:** Approved for planning
**Corpus snapshot:** `v2026.08`

---

## 1. Summary

OpenLaw.in is a free, open-source legal-aid chatbot for India. An ordinary person
describes a legal problem in their own language and receives a plain-language answer
in which **every legal claim is backed by a citation that resolves to a government
source document**.

The product is built on two open datasets held locally, combined so that each supplies
what the other lacks. It is free to use after email or Google signup, subject to a
monthly quota, and fully self-hostable by legal-aid bodies with no quota.

### The one-sentence differentiator

Every citation OpenLaw.in emits resolves to a government PDF, or it is not shown.
This is enforced structurally, not by prompting.

---

## 2. Problem and market gap

### Demand

From the India Justice Report 2025 and related NALSA data:

| Measure | Value |
|---|---|
| Population eligible for free legal aid | ~80% |
| People who actually used legal aid, 2023–24 | 15.5 lakh |
| Undertrials who used free legal aid | 7.91% |
| Legal aid share of the justice budget | <1% |

The gap between eligibility and use is the market.

### Competitive landscape

| Product | Case-law grounded | Free to citizens | Open source | Citizen-facing |
|---|---|---|---|---|
| KanoonGPT.in | No — statutes only, case law "coming soon" | No — ₹1,299/mo, 5 chats/week free | Dataset only | No — lawyers, students |
| Jhana.ai | Yes | No | No | No — lawyers |
| Lucio | Yes | No | No | No — law firms |
| NyayGuru | Claimed, not verifiable | Yes — 100 queries/mo | No | Yes |
| **OpenLaw.in** | **Yes, verified** | **Yes** | **Yes** | **Yes** |

Two observations drive positioning:

1. **KanoonGPT publishes the case-law dataset we are using, but its own product does
   not use case law yet.** The data is available to everyone; the product built on it
   is not.
2. **The free citizen-facing tools are closed-source and cannot prove their citations.**
   The failure mode of an ungrounded legal chatbot is not a wrong answer — it is a
   plausible fabricated citation. That is what we defend against.

---

## 3. Datasets

Both datasets are held locally at `/Users/sauravagarwal/Engineering/datasets/`.

### 3.1 `vaquill/open-india-law` — the text layer (50 GB, CC BY 4.0)

| Content | Volume |
|---|---|
| Judgment chunks (SC + 25 High Courts, 1950–2025) | 32,572,660 |
| Legislation provisions (22,265 enactments, Central + all States) | 544,078 |
| Regulator provisions (RBI, SEBI, MCA, TRAI, and 8 others) | 554,191 |
| Published Qdrant vectors | 32.5M, voyage-4, 1024-dim, 463.6 GB |

Published Qdrant collections (verified against `oss-data-in.vaquill.ai/qdrant/index.json`):

| Collection | Content | Shards | Size |
|---|---|---|---|
| `acts_india` | Legislation + regulator provisions | 2 | 11,207,628,288 B (10.44 GB) |
| `legal_corpus_v1` | **High Court judgments only** — older ingest, no citation field | 4 | 272,836,016,640 B (254.14 GB) |
| `legal_corpus_v2` | **Supreme Court + High Court judgments** — newer ingest, carries citations | 4 | 179,587,603,968 B (167.25 GB) |

> **The dataset README's collection table is wrong and must not be relied on.**
> It labels `legal_corpus_v1` as "High Court and Supreme Court" and
> `legal_corpus_v2` as "Tribunal and regulator decision chunks". Four documents
> inside the `open-india-law` repository contradict it and agree with each other:
>
> - `docs/pipeline/corpus-data-backfill.md` — v1 "~9.8M unique cases, HC only";
>   v2 "~5.9M unique cases, SC + HC"
> - `docs/audit/02-methodology.md` — v1 "High Court judgments, older ingest";
>   v2 "High Court and Supreme Court judgments, newer ingest"
> - `data/QDRANT_RESTORE.md` — v1 "no citation field"; v2 "Newer ingest, carries
>   citations"
> - `docs/audit/tribunals/README.md` — SC and HC judgments are the full-text
>   material in both collections
>
> `coverage.yml` records `embedded_without_text_pct` of 2.2 (v1) and 2.7 (v2),
> confirming both hold genuine full text. The collections overlap:
> `docs/audit/01-summary.md` notes that for most shared courts the v2 copy is a
> subset of v1, so restoring both requires deduplication by `case_id`.

**There are no tribunal vectors in any published collection** — see §3.5.

Judgment schema carries `case_id`, `chunk_id`, `chunk_index`, `text`, `court`,
`court_code`, `judges`, `title`, `petitioner`, `respondent`, `disposition`,
`citation`, `decision_date`, `language_code`.

Legislation schema carries `act_id`, `title`, `chapter`, `section_number`, `text`,
`jurisdiction`, `act_status`, `in_force`, `provision_type`, `legal_subject`,
`has_proviso`, `has_non_obstante`, `acts_referenced`, `defined_terms`, `source_url`.

### 3.2 `KanoonGPT/indian-case-laws` — the metadata layer (10 GB, Apache-2.0)

17,070,574 judgment-level records (one per judgment, not per chunk) carrying
`cnr_number`, `neutral_citation`, `law_report_citation`, `coram_members`,
`disposition_text`, `headnote_text`, `quality_json`, `source_json_s3_url`,
and **`source_pdf_s3_url`**.

### 3.3 Why both — the join

**vaquill's judgment `source_url` is empty.** 12.8M judgments with full text and no
citation back-link. KanoonGPT has the government PDF URL for every judgment, plus
official SCR headnotes and per-record parser quality flags.

The join was verified empirically, not assumed:

| Court set | Join key | Result |
|---|---|---|
| Supreme Court | vaquill `case_id` ↔ KanoonGPT `neutral_citation`, normalised (`2016_INSC_1151` ↔ `2016INSC1151`) | **100.0%** — 37,014 / 37,014 |
| Delhi High Court | vaquill `case_id` ↔ KanoonGPT `cnr_number` (`DLHC010517432014`) | **98.6%** — 325,898 / 330,401 |

Of 43,424 KanoonGPT Supreme Court records, **43,402 carry a headnote**.

This join is the foundation of the citation guarantee. Without it, no retrieved
judgment chunk can be turned into a verifiable citation.

#### Identifier formats to normalise

| Field | Format | Example |
|---|---|---|
| `case_id` (SC) | `YYYY_INSC_N` | `2016_INSC_1151` |
| `case_id` (HC) | CNR, 16-char alphanumeric | `DLHC010517432014` |
| `neutral_citation` | no separators | `2019INSC398` |
| `cnr_number` | 16-char alphanumeric | `ESCR010005442019` |
| `act_id` | `IND_<jurisdiction>_<id>` | `IND_central_2385` |
| `decision_date` (SC) | `YYYY-MM-DD` | `2019-03-26` |
| `decision_date` (HC) | `YYYY-MM-DD HH:MM:SS` — **differs from SC** | `2016-05-31 00:00:00` |
| `language_code` | 2–3 char | `en`, `hin`, `pun` |
| `legal_subject` | **string-encoded list** — must be parsed | `"['tax_law', 'family_law']"` |

### 3.4 Measured data quality issues

These were found by inspecting the data and must be handled in the pipeline.

1. **Supreme Court is only 33.3% English.** Of 1,113,313 SC chunks, 371,247 are
   English; the remainder are court-produced translations of the same judgments
   (Punjabi 83,542, Hindi 83,471, plus 9 more languages in the sampled 300K).
   Filtering to English retains **35,596 of 37,014 cases — 96.2%** while removing
   67% of the volume. Without this filter, retrieval returns the same case several
   times in languages the user did not ask for.

2. **High Court judgments are 100% English.** The multilingual duplication problem is
   Supreme-Court-only. Verified across Delhi, Bombay, Madras, Telangana, Allahabad
   and Karnataka (12,674,881 chunks, all `language_code == 'en'`).

3. **OCR quality varies materially.** A sampled 1979 SC judgment in Punjabi contained
   visible mojibake. English text carries stray Supreme Court Reports margin letters
   (`A` through `H`) inline. Judge extraction has parser noise — observed values
   include `judges: ['which']` and `['SH. ANIL KUMAR SISODIA', 'vide']` in Delhi HC.

4. **Semantic section labels are effectively unusable.** In 300,000 sampled SC chunks:
   `body` 149,163, `paragraph` 122,518, `section` 25,278 — but `ratio_decidendi` 25,
   `obiter_dicta` 9, `precedents` 7. Retrieval must not depend on these to locate a
   holding.

5. **`legal_subject` is a coarse filter, not a router.** 100% populated across 544,078
   provisions, but 336,224 are tagged `general`. Distribution: `tax_law` 63,249,
   `property_law` 44,595, `administrative_law` 30,213, `criminal_law` 23,326,
   `labour_law` 15,496, `environmental_law` 15,491, `banking_finance` 9,181,
   `civil_procedure` 8,386, `family_law` 7,522, `corporate_law` 7,298,
   `constitutional_law` 2,890, `information_technology` 1,849,
   `intellectual_property` 1,044. **There is no consumer-law tag.**

6. **`section_title` in legislation is sometimes populated with the Act title** rather
   than the section heading; the true heading is inside `text`.

### 3.5 Known coverage limits — to be stated publicly, not concealed

- **Retrieval coverage ≠ corpus coverage.** `COVERAGE.md` records **1,786,868 more
  judgments held as extracted text than are embedded**. Rajasthan HC holds 1,057,775
  texts against 324,567 embedded. No "complete coverage" claim may be made.
- **Consumer fora (NCDRC and State Commissions) are absent from this corpus.**
  Consumer disputes are among the highest-volume citizen legal problems. v1 answers
  them from the Consumer Protection Act and Supreme Court precedent but cannot cite
  consumer-forum decisions. Launch messaging must not imply otherwise.
- **Tribunal and regulator decisions are not available as text.** 2,112,201 documents
  (542 GB) are held as PDFs only — `Extracted text: No`, "Case index searchable".
  RERA, CAT, DRT, NGT, NCLT, ITAT and CESTAT cannot be cited. See §4.
- **District and trial court decisions are not included** in the upstream corpus.
- **High Court judgments are not in v1** — deferred to phase 2 (§4).
- **Snapshots are point-in-time.** `v2026.08` is an archive, not current law.

---

## 4. v1 corpus scope

**1,469,516 vectors.**

| Source | Vectors | Method | Cost |
|---|---:|---|---|
| `acts_india` — 22,265 enactments, all sections, regulator instruments | 1,098,269 | Restore published snapshot (10.44 GB) | Free |
| Supreme Court, English only — 35,596 cases | 371,247 | Re-embed with voyage-4 | ~$20–35 |

**Supreme Court is re-embedded rather than restored.** `legal_corpus_v2` does contain
Supreme Court chunks with citations, but it holds 11,823,753 SC **and** High Court
vectors across four shards that cannot be split by court, so obtaining 371,247 SC
chunks would mean a 167.25 GB download and a 32 GB-RAM box. Re-embedding ~280M tokens
for ~$20–35 is dramatically cheaper and yields exactly the wanted slice. Embedding
**must** use voyage-4 to stay in the same vector space as `acts_india`; vectors from a
different model are not comparable.

**High Courts are deferred to phase 2.** When they are added, the source should be
**`legal_corpus_v2` (167.25 GB, carries citations), not `legal_corpus_v1`
(254.14 GB, no citation field)** — v2 is smaller and richer. Note the two collections
overlap and would need deduplication by `case_id` if both were ever restored.

### Tribunals are out of scope — the data does not exist

An earlier draft of this spec included tribunal and regulator decisions
(RERA, CAT, DRT, NGT, NCLT, ITAT, CESTAT) on the strength of the dataset README's
claim that `legal_corpus_v2` held "Tribunal and regulator decision chunks". **That
claim is false.** `legal_corpus_v2` holds Supreme Court and High Court judgments
(§3.1), and no published collection contains tribunal vectors.

`COVERAGE.md` states the position directly: tribunal and regulator documents are
2,112,201 files, 542 GB, `Original PDF: Yes`, **`Extracted text: No`**, status
**"Case index searchable"**. Every one of the 15 forums is marked `Searchable: Case
index`. Only ~348,516 of those documents (16%) are even reasoned decisions rather than
procedural order sheets; extracting them is a large OCR job that upstream has not done.

This is a genuine loss. RERA, CAT and DRT map onto very common citizen problems —
homebuyer disputes, government service matters, and borrowers facing bank recovery.
v1 cannot serve them from tribunal decisions and must not imply that it can.

**Resolved without downloading anything.** This finding replaces the Phase 0 kill-gate
that earlier drafts specified (restore one 42 GB shard and inspect payloads); the
repository's own documentation answered the question.

### Infrastructure

- Qdrant, self-hosted, int8 scalar quantization, `on_disk` vectors.
- ~1.5 GB RAM for quantized vectors; provision 8–16 GB.
- ~15 GB restored index; provision 100 GB SSD.
- A modest VM (~$40–80/mo) is sufficient. The 32 GB / 400–500 GB dedicated-server
  sizing in earlier drafts applied to the tribunal scope and no longer applies.

---

## 5. Knowledge graph — deterministic

**Decision: deterministic graph only in v1. LightRAG is deferred to v2.**

### Rationale

LightRAG performs LLM entity/relation extraction per chunk, so its cost scales directly
with corpus size. Across the v1 corpus of **1,469,516 chunks** that is roughly 1.1B
input tokens plus generation — on the order of **$3,000–7,000** with gleaning passes,
re-incurred on every corpus refresh. Should High Courts be added in phase 2, the figure
rises by more than an order of magnitude.

It would also re-derive, at lower precision, edges that already exist as clean columns.

### Free edges, measured

| Edge | Count / density |
|---|---|
| Provision → Act (`acts_referenced`) | 543,390 across 544,078 provisions |
| Provision → defined term (`defined_terms`) | 296,875 |
| Judgment → cited case (regex over text) | present in **84.8%** of English SC chunks |
| Judgment → statutory section (regex) | present in **48.4%** of English SC chunks |
| Judgment → judge / court / disposition / year | 100% |

### Node and edge model

Nodes: `Act`, `Section`, `Judgment`, `Court`, `Judge`, `LegalSubject`, `DefinedTerm`.

Edges: `Section —REFERENCES→ Act`, `Section —DEFINES→ DefinedTerm`,
`Section —HAS_SUBJECT→ LegalSubject`, `Judgment —CITES→ Judgment`,
`Judgment —INTERPRETS→ Section`, `Judgment —DECIDED_BY→ Judge`,
`Judgment —IN_COURT→ Court`.

### Storage: Neo4j

**Stored in Neo4j Community Edition** (current stable `2026.06.0`), as a separate
service reached over the Bolt protocol.

Graph size is modest — ~1.3M nodes, ~1.9M edges — and Postgres recursive CTEs would
handle the 1–2 hop retrieval-time expansion. Neo4j is chosen for what it enables beyond
that:

1. **Variable-depth precedent lineage.** `MATCH path = (a:Judgment)-[:CITES*1..6]->(b)`
   is native in Cypher and awkward in SQL.
2. **PageRank over the citation network** (GDS product tier, available in Community
   Edition) gives authority ranking — landmark precedents surface by citation
   centrality rather than keyword match.
3. **Community detection (Louvain/Leiden) recovers doctrinal clustering** that the
   metadata does not carry. Given `legal_subject` is 62% `general` with no consumer-law
   tag (§3.4.5), inferring subject clusters from citation structure is a direct
   mitigation for a documented data weakness.
4. **LightRAG (v2) has a native Neo4j storage backend**, making the conceptual-graph
   phase a configuration change rather than a re-platform.

The graph is **read-only at request time** — written only by the batch builder, once per
corpus release. There is therefore no runtime dual-write consistency concern between
Neo4j and Postgres.

**Operational notes:**
- Community Edition is **GPLv3** (see §10, Licensing).
- GDS Community Edition caps concurrency at 4 CPU cores. Irrelevant here: PageRank and
  community detection are offline batch precomputes over ~1.9M edges, not request-path
  work.
- No clustering and no role-based access control in Community Edition. Acceptable —
  the graph is a rebuildable derived artifact, so its availability requirement is low
  and it holds no user data.
- Sizing: ~4 GB page cache, ~4 GB heap.
- Ship a Docker Compose file covering Postgres + Qdrant + Neo4j so the three-service
  stack does not compromise the self-hosting success criterion (§12.7).

**Reversibility:** the graph is derived data rebuilt by a batch job from parquet. If
Neo4j proves wrong, the same builder targets another store. This is a cheap decision to
revisit, unlike the voyage-4 embedding dependency (§13.2).

### Deferred to v2

LightRAG over a curated citizen-relevant subset (SC cases in family, tenancy, labour,
consumer and criminal matters — roughly 50–80K chunks, ~$150–350), to add conceptual
entities and multi-hop paths that regexes cannot recover. Gated on evaluation showing
real multi-hop retrieval failures.

---

## 6. The answer contract

**Every citation the model emits must resolve to a chunk retrieved in that same turn.**

### Pipeline

```
user question (any supported language)
  │
  ├─ classifier model  → intent, complexity, crisis detection
  ├─ translator model  → query translated to English
  │
  ├─ hybrid retrieval  → voyage-4 dense + published sparse BM25
  │                      Qdrant payload filters (court, year, in_force, jurisdiction)
  ├─ graph expansion   → cited sections, referenced Acts, related provisions
  ├─ rerank            → cross-encoder
  │
  ├─ generator model   → structured output: claims + supporting chunk_ids
  │
  ├─ CITATION VALIDATOR (deterministic, no LLM call)
  │     each claim's chunk_ids must be a subset of this turn's retrieval set,
  │     else the claim is dropped and the event logged
  │
  ├─ translator model  → validated English answer → user's language
  │                      (statutory quotes retained in English alongside)
  │
  └─ render            → answer + source cards linking to government PDFs
                       + corpus date + DLSA/NALSA escalation
```

### Why structured outputs

The generator returns `output_config.format` JSON — a list of claims, each carrying the
`chunk_id`s that support it — rather than prose with inline citations. The validator
then performs set membership checking, not parsing. This makes verification
deterministic, lets unsupported claims be dropped individually rather than discarding
the whole answer, and costs no additional LLM call.

### Why validate before translating

Generation and validation happen **in English**; translation operates on already-verified
text. If the model composed directly in the user's language, the validator would have to
reason across languages. Keeping the safety-critical step monolingual means translation
cannot introduce an unverified citation.

### Behaviour on weak retrieval

If retrieval does not support an answer, the system says so explicitly and routes the
user to legal aid. It does not generate unsupported guidance.

### Hallucination logging

Every dropped citation is recorded with the question, retrieval set, and model output.
This is both a safety metric and a labelled evaluation dataset produced free by
production traffic.

---

## 7. Multilingual

**Multilingual in and out; retrieval in English.**

The corpus we index is English. The user's language is handled at the edges: query
translated in, answer translated out. Court-produced regional translations of judgments
are *not* indexed — they duplicate cases already covered by their English text and
carry the worst OCR quality.

Statutory quotations are shown in English alongside the translation, because the English
text is the authoritative one and a translated statute is not the statute.

---

## 8. Model configuration — per role

Models are configurable **per pipeline role**, not globally. Translation does not need
the reasoning capability that grounded legal answering needs, and paying frontier prices
for it is waste.

```yaml
models:
  generator:   claude-opus-5       # grounded answer + citations
  translator:  claude-haiku-4-5    # query → EN, validated answer → user language
  classifier:  claude-haiku-4-5    # intent, complexity routing, crisis detection

  # optional per-language translator override
  translator_overrides:
    ta: <Indic-specialist model>
    mr: <Indic-specialist model>
```

Every role is independently overridable and provider-agnostic (Anthropic, OpenAI,
Gemini, or a locally hosted model) behind a single adapter interface. Adding a role must
not require touching call sites; roles resolve through the adapter registry.

### Cost basis

Current pricing per 1M tokens: Opus 5 `claude-opus-5` $5 / $25; Sonnet 5
`claude-sonnet-5` $2 / $10; Haiku 4.5 `claude-haiku-4-5` $1 / $5.

Per question at ~7,600 input / ~800 output tokens:

| Generator model | Per question | 1K users @30/mo | 10K users @30/mo |
|---|---:|---:|---:|
| Haiku 4.5 | $0.0116 (≈₹1.0) | $348/mo | $3,480/mo |
| Sonnet 5 | $0.0232 (≈₹2.1) | $696/mo | $6,960/mo |
| Opus 5 | $0.058 (≈₹5.2) | $1,740/mo | $17,400/mo |

Translation adds ~300 in / ~300 out per direction — ~$0.0018 on Haiku versus ~$0.009 on
Opus. Role separation is what makes that saving available.

Recommended hosted configuration: two-tier generator routing — Haiku 4.5 for
straightforward statutory lookups, Opus 5 for multi-issue questions where reasoning
quality changes the advice.

### Prompt caching

Render order is `tools → system → messages`; any byte change invalidates everything
after it. The system prompt, answer-format rules and legal glossary sit **before** the
cache breakpoint. Retrieved chunks — different every turn — sit **after** it. Caching
retrieved context would yield a 0% hit rate. Verify with
`usage.cache_read_input_tokens`.

---

## 9. Application architecture

```
Next.js (web)  ──►  FastAPI (Python)  ──►  Qdrant   (vectors)
                          │             ──►  Neo4j    (knowledge graph, read-only
                          │                            at request time)
                          │             ──►  Postgres (users, quota, conversations,
                          │                            hallucination log)
                          └──►  LLM adapter (per-role, provider-agnostic)
```

- **Backend:** Python / FastAPI, type-hinted throughout, `pathlib.Path`, small focused
  modules organised by domain.
- **Neo4j:** knowledge graph (§5), Community Edition, Bolt protocol, rebuilt per corpus
  release by a batch job.
- **Postgres:** users, quota counters, conversations, hallucination log.
- **Qdrant:** vectors, self-hosted, with payload filters for court, year, jurisdiction
  and `in_force`.
- **Auth:** Supabase — Postgres plus email and Google OAuth in one dependency.
- **Deployment:** Docker Compose covering all three datastores, so self-hosting remains
  a single command.

### Access and quota

- Anonymous: 3 questions before signup, to remove friction.
- Signed in (email or Google): 30 questions/month, resetting.
- Self-hosted: quota disabled by configuration flag.

Metering ships in v1. It is cheap to build early and painful to retrofit.

---

## 10. Safety and trust

This is legal information for vulnerable people. These are requirements, not polish.

1. **Every answer states that it is legal information, not legal advice**, and cannot
   replace a lawyer.
2. **Escalation is a feature.** Every answer ends with the user's nearest DLSA/NALSA
   contact and the NALSA helpline (15100). Routing someone to a human is a success
   state, not a failure state.
3. **Refuse and route on weak retrieval** rather than generating unsupported guidance.
4. **Hard-coded crisis routing** for domestic violence, custodial abuse, trafficking and
   suicidality, surfacing helpline numbers immediately and ahead of any legal analysis.
5. **Never solicit identifying details** — no names, case numbers, or Aadhaar.
   Conversations encrypted at rest and user-deletable.
6. **Time-bound answers.** Every answer carries the corpus snapshot date (`v2026.08`)
   and links to the live official source.
7. **Log every stripped citation** as a safety metric and evaluation input.
8. **Publish coverage limits** — the embedded-vs-held gap, the absence of consumer fora,
   and the exclusion of district courts.

### Attribution and licensing

**Data.** `vaquill/open-india-law` is CC BY 4.0 and **requires attribution**, a condition
imposed by upstream eCourts/NCLT/NCLAT policy rather than by vaquill.
`KanoonGPT/indian-case-laws` is Apache-2.0. Both must be credited in the product and
repository.

**Neo4j Community Edition is GPLv3.** It runs as a **separate service reached over the
Bolt network protocol**, which is the standard arrangement and keeps OpenLaw.in's own
code free to carry a permissive licence (Apache-2.0 recommended). Two consequences to
handle explicitly:

- Any distributed Docker image bundling Neo4j CE ships GPLv3 software and must carry the
  licence and source offer for that component.
- Downstream adopters wanting to build closed commercial products on the stack will meet
  GPL friction at the Neo4j layer. This does not conflict with the project's mission,
  but it should be documented rather than discovered.

This is a licensing summary, not legal advice; confirm before any distribution that
matters.

---

## 11. Build plan

| Phase | Deliverable | Gate |
|---|---|---|
| **0. Verify** | ~~Restore one `legal_corpus_v2` shard~~ — **resolved from documentation, 2026-09-08**: tribunals have no extracted text and drop from scope (§4) | Closed. No download required |
| **1. Data** | Join pipeline (SC neutral citation, HC CNR); English filter; quality filter; `acts_india` restore; SC re-embed | Every indexed chunk resolves to a government URL |
| **2. Graph** | Deterministic knowledge graph in Neo4j; batch builder; GDS PageRank (authority) and Louvain (doctrinal clusters) precomputed | 543K act-references and the citation network queryable; 1–2 hop expansion returns in <100ms |
| **3. Retrieval** | Hybrid dense + sparse, graph expansion, reranking | Measured recall on a ~200-question citizen eval set |
| **4. Answers** | Structured-output generation + citation validator | **Zero unverifiable citations** on the eval set |
| **5. Multilingual** | Query and answer translation, legal glossary, per-language model config | Native-speaker review per language |
| **6. Application** | Auth, quota, chat UI, escalation routing | End-to-end working product |

Development is test-first throughout, with a minimum of 80% coverage, per project
standards.

---

## 12. Success criteria

**v1 ships when:**

1. Zero unverifiable citations across the full evaluation set.
2. Every displayed citation resolves to a reachable government URL.
3. Retrieval recall@10 ≥ 0.85 on the ~200-question citizen eval set (the gold chunk
   appears in the top 10 retrieved for at least 85% of questions).
4. Answers are returned in at least Hindi and English, reviewed by native speakers.
5. Crisis routing fires correctly on every red-team crisis prompt.
6. A user can sign up with email or Google and ask a question in under 60 seconds.
7. Someone other than the author can self-host from the README.

---

## 13. Open risks

1. **The evaluation set is the hard, unglamorous prerequisite.** ~200 real citizen legal
   problems with known-correct answers are the only way to know whether any of this
   works. Sourcing them — ideally with legal-aid clinics — is on the critical path.
2. **voyage-4 is a hard vendor dependency** for query embedding, since the published
   vectors live in its space. Escaping it means re-embedding the corpus.
3. **Upstream documentation is unreliable and must be cross-checked.** The dataset
   README mislabels both judgment collections (§3.1), and an earlier draft of this spec
   built a whole corpus tier on that error. Trust the `open-india-law` repository's
   audit and pipeline docs over the published README, and verify any coverage claim
   against `COVERAGE.md` or `coverage.yml` before designing around it.
4. **Cost scales linearly with success.** At 10K active users on Opus 5 the LLM bill is
   ~$17,400/month. The quota and the two-tier routing are the controls; funding needs to
   be in place before promotion.
5. **Corpus staleness.** `v2026.08` is a snapshot built by 54 scrapers against government
   portals that change without notice. A refresh strategy is required before the data
   ages materially.
6. **The forum gap is now the largest functional risk.** Neither consumer fora
   (NCDRC and State Commissions, never collected) nor tribunals (collected as PDFs but
   never text-extracted, §4) can be cited. Between them these cover homebuyer disputes,
   consumer complaints, government service matters and bank recovery — a large share of
   what citizens actually walk in with. v1 answers them from statutes and Supreme Court
   precedent alone. GDS community detection (§5) partly mitigates the related
   `legal_subject` weakness but cannot invent decisions the corpus does not contain.
   Closing this gap means OCR at scale, which is a project in itself.
7. **Three datastores raise the self-hosting bar.** Postgres, Qdrant and Neo4j must come
   up together for a legal-aid body with limited technical capacity. Docker Compose is
   the mitigation, and §12.7 is the test of whether it worked.
8. **Neo4j Community Edition is GPLv3** (§10). Not a conflict with an open-source
   mission, but it constrains downstream commercial reuse and must be documented.

---

## 14. Explicitly out of scope for v1

- High Court judgments — phase 2, sourced from `legal_corpus_v2` (167.25 GB, carries
  citations), not `legal_corpus_v1`.
- Tribunal and regulator decisions — **not deferred but unavailable**: held as PDFs
  with no extracted text (§4). Blocked on upstream OCR, not on our scoping.
- LightRAG conceptual knowledge graph — v2.
- Document drafting, document upload, case tracking.
- Lawyer-facing research tooling.
- Mobile applications.
- Any paid tier.
