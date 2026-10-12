# Retrieval sweep — embedding and reranker models, measured offline

**Date:** 2026-10-12 · **Status:** draft for review · **Related:** #216 (embedding A/B,
v2026.10.0), #497 (reranker default), #498 (k = 5 → 8), #396 (feature-matrix campaign),
#215 (GPU embedding service), #130 (Bedrock rerank, parked)

## 1. Purpose

archi retrieves in two stages. Stage 1 is a hybrid search: Postgres BM25 plus vector
similarity, top 20 child chunks. Stage 2 is a FlashRank cross-encoder that reorders the 20
and returns 5 parent sections
(`src/data_manager/vectorstore/retrievers/hierarchical_retriever.py:236-290`). Both models
are small: `all-MiniLM-L6-v2` reads 256 tokens of a 512-token child chunk, and
`ms-marco-MiniLM-L-12-v2` is a 33M-parameter web-search model.

The end-to-end campaign (#396) costs about 7 hours of GPU-host time for each arm, and the
LLM and the judge add noise that hides retrieval effects. This sweep measures retrieval
**alone**, on claw, in minutes for each arm, with no LLM. It finds the configurations that
are worth an end-to-end run. It does not change a default.

**Success:** a ledger with one row for each arm, a report that ranks the arms on a
pre-registered metric, and at most 3 finalists confirmed on held-out questions. Each
finalist goes into a GitHub issue as a work order for an end-to-end run on the GPU host.

## 2. What we said, what we assume

Said by the operator: try different reranker and embedding models; look for gains; be
methodical; iterate fast; govern the run with `/goal`.

Assumed (correct these if wrong):

- The sweep runs on **claw only**, against the `postgres-claw` corpus, **read-only**.
  No redeploy, no GPU host, no change to product code or to `archi-config`.
- The question bank is `config/benchmarking/fasrc_ragas_queries.json` (105 rows; 102 have
  gold `sources`; the 3 `should_refuse` rows are out of scope). Measured 2026-10-12: every
  one of the 102 rows has at least one gold URL in the claw corpus.
- A retrieval "hit" is document-level: a returned chunk or parent whose document URL
  equals a gold URL (trailing slash removed).
- CPU only. claw has 16 cores, 62 GB RAM, about 21 GB free, and no GPU. Production runs
  the reranker and the query embedder on CPU in the chatbot container, so CPU latency is
  the correct cost to measure.

## 3. Facts the design depends on

- **Stage 1 scores every chunk.** `PostgresVectorStore.hybrid_search`
  (`src/data_manager/vectorstore/postgres_vectorstore.py`) computes cosine similarity and
  pg_textsearch BM25 for every row of `document_chunks`, min-max normalizes each over the
  whole table, and combines them with `semantic_weight` and `bm25_weight`. There is no
  approximate index. As a result, an offline copy can match it exactly: the BM25 scores do
  not depend on the embedding model, so the script reads them from Postgres once for each
  question and caches them.
- **Claw corpus, 2026-10-12:** 852 live documents, 6,212 child chunks, all embedded with
  `sentence-transformers/all-MiniLM-L6-v2` (384 dims). `document_parent_nodes` holds 15,826
  rows because it keeps the parents of every past ingest
  (`scripts/benchmarking/dump_chunk_overlap_corpus.sql` header). Only parents that a live
  child references are used.
- The claw corpus is not the dev corpus (1,091 documents). Results are comparisons
  **inside** claw. The end-to-end run on dev confirms them.
- The archi conda env (`/home/austin/miniforge3/envs/archi`) already has
  `sentence_transformers`, `flashrank`, `torch`, and `onnxruntime`. The sweep adds no
  dependency.
- `scripts/benchmarking/paired_tests.py` already has `mcnemar_exact`, `holm_adjust`, and
  `paired_binary`. The sweep reuses them.

## 4. Approaches considered

1. **Offline harness on a frozen snapshot (recommended).** Dump the corpus once, cache BM25
   scores and embeddings, and compute both stages in Python. An arm that changes only the
   reranker, the weights, or the pool size runs in seconds to minutes. A new embedding
   model costs one re-embed of 6,212 chunks on CPU.
2. **Re-ingest claw for each arm and call the live retriever.** Most faithful, but each
   embedding arm costs a full ingest (about 30 minutes with a live re-scrape, which also
   changes the corpus) and needs a schema change for each new dimension. Too slow, and it
   breaks the fixed-corpus rule.
3. **Live retriever with a scratch pgvector table for each model.** Faithful SQL, no
   re-scrape, but it writes to Postgres for each arm. It adds risk and no information that
   approach 1 lacks after a parity check.

Approach 1 wins on speed and safety. Its one risk, a drift from production math, is closed
by the parity gate in §6.1.

## 5. Components

All new code lives in `scripts/benchmarking/retrieval_sweep/`, with unit tests in
`tests/unit/benchmarking/test_retrieval_sweep_*.py`. Each unit has one job.

| Unit | Job | Input → output |
| --- | --- | --- |
| `snapshot.py` | Read-only dump of live child chunks (id, text, document URL, parent id), the parents they reference, and the bank's BM25 score vectors | `postgres-claw` → `snapshot/` (`chunks.jsonl.gz`, `parents.jsonl.gz`, `bm25.npz`, `manifest.json` with a sha256 fingerprint) |
| `embed_cache.py` | Embed chunks and queries for one model spec; cache by (model id, pinned revision, max tokens, prefixes, snapshot fingerprint) | snapshot + model spec → `cache/emb/<key>.npy` |
| `stage1.py` | The `hybrid_search` math: cosine, min-max over the whole table, weighted sum, ties broken by chunk id | embeddings + BM25 + weights + pool size → ranked chunk ids |
| `stage2.py` | Rerankers behind one interface: `none`, FlashRank (by model name), sentence-transformers `CrossEncoder` (by Hugging Face id). Then the parent mapping and de-duplication of `_get_relevant_documents` | query + candidates → top-k documents |
| `metrics.py` | Per-question scores and aggregates | ranked lists + gold URLs → metrics |
| `run_arm.py` | Run one arm from a YAML spec; append one row to the ledger | arm spec → `ledger.jsonl` row |
| `report.py` | Rank arms, run paired tests against the baseline, write the report | ledger → `report.md` |

**Model specs.** Each embedding model spec records its Hugging Face id, a **pinned commit
revision**, `max_seq_length`, the query and document prefixes (for example
`search_query: ` and `search_document: ` for nomic, `query: ` and `passage: ` for e5), and
`trust_remote_code`. A model that needs `trust_remote_code` (the `gte-*-v1.5` models, nomic)
loads only at a pinned revision, so no unpinned remote code runs.

**Output location.** Snapshots, caches, and the ledger go to
`bench_out/retrieval_sweep/<date>/`, which is the separate `fasrc/archi-bench-out`
repository. Nothing large goes into archi.

## 6. Method

### 6.1 Round 0 — trust the harness (gate for every later round)

1. **Parity, stage 1.** For each of the 102 questions, call the live
   `PostgresVectorStore.hybrid_search` (archi env, `postgres-claw`, read-only, MiniLM,
   weights 0.4 and 0.6, k = 20) and compare with `stage1.py`. Pass: the same set of 20
   chunk ids for at least 100 of the 102 questions, and every difference is a tie on
   `combined_score` within 1e-6.
2. **Parity, stage 2.** Call the live `LlamaIndexHierarchicalRetriever` for the same
   questions and compare the top 5 parent ids. Pass: identical for at least 100 of 102,
   and every difference is a tie.
3. **Determinism.** Run the baseline arm twice. Pass: the two ledger rows have identical
   per-question results.

If any check fails, the sweep stops and reports the difference. No later number is
trusted until Round 0 passes.

### 6.2 Split and pre-registration

- **Split:** a stratified split of the 102 questions by `anchor_type`, keyed on sha256 of
  `user_input`: 68 **tune** and 34 **holdout**. Commit the split file before the first
  arm runs. Every choice uses tune only.
- **Primary metric:** final **Hit@5**, the share of questions whose top-5 documents include
  a gold URL. This is what the LLM sees.
- **Secondary metrics:** stage-1 Recall@20, @50, and @100 (does stage 1 find a gold
  document at all); MRR@5; Hit@8 (for #498); distinct documents in the top 5; CPU latency
  (query embedding ms, rerank ms for each question at p50 and p95); corpus embed time.
- **Comparison:** each arm against the baseline on the same questions, paired: exact
  McNemar on per-question Hit@5 (`paired_tests.mcnemar_exact`), Holm-adjusted across the
  arms of a round (`holm_adjust`).
- **Finalist rule (proposed thresholds; change them before Round 1 or not at all):**
  - Tune Hit@5 at least **+4 questions net** over the baseline, Holm-adjusted p < 0.10.
  - Rerank p95 at most **1,500 ms** per question on claw CPU; query embedding p95 at most
    **200 ms**.
  - At most **3 finalists**.
- **Holdout confirmation:** each finalist runs **once** on the 34 holdout questions. A
  finalist is confirmed if its holdout Hit@5 is at least the baseline's. The holdout is
  never used to choose anything.

**Known limit:** each question names one or two gold URLs. A different page that also
answers counts as a miss. This biases every arm the same way, so comparisons stay fair,
but absolute numbers are low estimates. Phase 3 (end-to-end) is the check on that.

### 6.3 Rounds — one factor at a time, then combine

Baseline arm **B0**: MiniLM-L6, weights semantic 0.4 / BM25 0.6, pool 20, FlashRank
`ms-marco-MiniLM-L-12-v2`, k = 5. Each round fixes everything except one factor.

| Round | Factor | Arms | Notes |
| --- | --- | --- | --- |
| R1 | Embedding model (reranker off, pool 100, read Recall@20/50/100) | `all-MiniLM-L6-v2` (B0), `BAAI/bge-small-en-v1.5` (384), `intfloat/e5-small-v2` (384), `BAAI/bge-base-en-v1.5` (768), `Alibaba-NLP/gte-base-en-v1.5` (768, 8k), `nomic-ai/nomic-embed-text-v1.5` (768, 8k), `Snowflake/snowflake-arctic-embed-m-v1.5` (768) | Large tier (`bge-large-en-v1.5`, `gte-large-en-v1.5`, `mixedbread-ai/mxbai-embed-large-v1`) runs **only if** a 768-dim model beats B0 on tune Recall@20 by ≥ 4 questions |
| R2 | Hybrid weight | `semantic_weight` ∈ {0.0, 0.2, 0.4, 0.6, 0.8, 1.0} for B0's embedder and the top 2 from R1 | No re-embed; seconds per arm |
| R3 | Reranker (best stage 1 from R1–R2, pool 20) | none; FlashRank `ms-marco-MiniLM-L-12-v2` (B0), `ms-marco-TinyBERT-L-2-v2`, `rank-T5-flan`; `cross-encoder/ms-marco-MiniLM-L-6-v2`; `BAAI/bge-reranker-base`; `BAAI/bge-reranker-v2-m3`; `mixedbread-ai/mxbai-rerank-xsmall-v1`, `mxbai-rerank-base-v1` | Record rerank latency for every arm |
| R4 | Pool size and reranker input | pool ∈ {20, 50, 100} × top 2 rerankers; rerank the child text (today) or the parent text | Answers the "20 is too few" question directly |
| R5 | Combine and confirm | Top 3 full configurations by tune Hit@5 that obey the latency budget → holdout | Ends the sweep |

"Best stage 1" means the highest tune Recall@20 among arms with query embedding p95 at most
200 ms; a tie goes to the smaller model. "Top 2" uses the same order.

About 40 arms in total. Cost estimate on claw CPU: a small or base embedding model takes
5–15 minutes to embed the corpus; a large one, 30–60 minutes; a base reranker over
102 × 20 pairs, 1–5 minutes; `bge-reranker-v2-m3` over 102 × 100 pairs, up to 40 minutes.
Expected total wall time: 6–10 hours, most of it in R1.

### 6.4 Ledger row

Each row records: arm id, round, the full arm spec and its sha256, model ids **and**
resolved revisions, snapshot fingerprint, archi git SHA, split file sha256, per-question
results (ranked document URLs, hit flags, ranks), aggregates for tune and holdout
separately, and timings. A row with a different snapshot fingerprint from B0's is
rejected by `report.py`.

## 7. Error handling

- A model that fails to download or load: the ledger gets a row with `status: error` and
  the message; the round continues.
- An arm that breaks the latency budget still gets a full row; it is just not a finalist.
- Snapshot drift: `run_arm.py` re-checks the snapshot fingerprint before each arm.
- Postgres is read-only for the whole sweep: `snapshot.py` and the parity check open a
  read-only transaction (`SET TRANSACTION READ ONLY`).

## 8. Testing (TDD, before each unit)

- `stage1.py`: a 5-chunk fixture with hand-computed min-max scores, including a constant
  column (the SQL's `NULLIF` returns 0) and a tie.
- `stage2.py`: parent mapping with two children of one parent, a child with no parent
  (passes through), and k smaller than the number of parents; a fake reranker.
- `metrics.py`: Hit@k, Recall@k, MRR@k with trailing-slash URL variants and a question
  with two gold URLs.
- `embed_cache.py`: cache key changes when the revision, a prefix, or the fingerprint
  changes; a fake encoder, no download.
- Split: deterministic, stratified, 68 / 34.
- The gate (`bash scripts/gate.sh`) must pass with patch coverage of 80% or more.

## 9. Out of scope

- Any change to a default, to `base-config.yaml`, or to `archi-config`.
- Any run on the GPU host, any redeploy, any LLM or judge call.
- Chunk-size changes (they need a re-chunk; #489 and #412 cover them).
- Bedrock Cohere Rerank (#130, parked).

## 10. After the sweep (Phase 3, a human decision)

For each confirmed finalist, file one issue (label `gpu`) as a work order for an
end-to-end run on the GPU host: the finalist config, its tune and holdout numbers, and the
`qa_arm.sh` commands. Post the report link on #216 and #497. If a finalist needs a
reranker bigger than CPU can serve, it joins #215 (the GPU embedding service can also
serve rerankers).

## 11. Governance with `/goal`

The sweep runs as a background session with a `/goal` condition, launched by
`~/.claude/skills/goal-merge/launch-goal.sh`:

```bash
~/.claude/skills/goal-merge/launch-goal.sh /home/austin/Projects/archi retrieval-sweep \
  docs/superpowers/specs/2026-10-12-retrieval-sweep-goal.md
```

The condition is in `2026-10-12-retrieval-sweep-goal.md` (under 4,000 bytes). It has one
measurable end state, the command that proves it, the constraints, a time bound, and a
`GOAL BLOCKED:` exit for a failed parity gate or a human decision. Each round ends with a
committed ledger, so a stopped goal leaves usable results.
