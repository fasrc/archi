# Hierarchical-rerank A/B (issue #32, task 6.2)

A two-arm benchmark measuring the #31 hierarchical-rerank retriever against the
pre-#31 baseline, on the FASRC corpus. The arms differ in **one** dimension:

| | Chunking | Retriever | Returns |
|---|---|---|---|
| `baseline_character_hybrid.yaml`  | `character` (CharacterTextSplitter) | `HybridRetriever` | child chunks |
| `treatment_hierarchical_rerank.yaml` | `sentence` (parent/child structural) | `LlamaIndexHierarchicalRetriever` (FlashRank rerank) | parent context |

Everything else — embedding model, BM25/semantic weights, SUT model, RAGAS judge,
question bank — is held identical, so the comparison stays apples-to-apples.

## Why two separate runs (not one `-cd` directory)

This A/B varies **ingestion and retrieval** config (chunking strategy + retriever),
not the prompt. The `archi evaluate -cd` directory mode is a *prompt sweep over a
single, once-ingested corpus*: its loader (`ConfigurationManager._append`) requires
the whole `global` block — including `DATA_PATH` — to be identical across configs,
and the runtime retriever/chunking come from the once-seeded Postgres config, not
each arm's YAML. So a single `-cd` run **cannot** vary chunking/retriever per arm;
both arms would silently share one ingest and one retriever.

Instead, run each arm as its **own** deploy + ingest + evaluate pass and compare
the two RAGAS aggregates offline.

## Run it

```bash
mkdir -p bench_out/hierarchical_rerank_ab

# Arm 1 — baseline: deploy + ingest with this config, then evaluate
archi evaluate -n hr-ab-baseline \
  -c examples/benchmarking/hierarchical_rerank_ab/baseline_character_hybrid.yaml \
  -e ~/.archi/.env.benchmark --hostmode

# Arm 2 — treatment: redeploy + re-ingest with this config, then evaluate
archi evaluate -n hr-ab-treatment \
  -c examples/benchmarking/hierarchical_rerank_ab/treatment_hierarchical_rerank.yaml \
  -e ~/.archi/.env.benchmark --hostmode
```

Each pass ingests its own vectorstore at its own `global.DATA_PATH` and writes its
own dump JSON (each arm's four RAGAS metrics + per-question `time_elapsed`).

Prereqs (this is a `needs-deploy` task — the local gate cannot run it):
- FASRC VPN up (the vLLM SUT + HuggingFace embeddings need split-DNS).
- `HUIT_API_KEY` in `~/.archi/.env.benchmark` for the Bedrock judge.
- **Operator-local config** (the `config/` tree is gitignored): the configs reference
  `agent_md_file: config/agents/fasrc-cannon.md` and a corpus list under
  `data_manager.sources` — stage both on the host before running, or repoint them
  at checked-in equivalents (e.g. an `examples/agents/*.md` spec).

## What #32 wants out of this

1. **Quality** — the four RAGAS metrics per arm (one aggregate per run). Watch the
   `reasoning` questions especially: returning parent context should help synthesis
   more than single-fact lookups (use `fasrc_ragas_queries.json` to slice by
   `anchor_type`).
2. **Latency** — each run reports per-question `time_elapsed`. The treatment's
   **first** query pays a one-time FlashRank ONNX model load (~45s on dev vs ~8s
   baseline). To report *warm* latency, discard the first treatment question (or
   prepend a throwaway warmup question to the bank) and average the rest.
3. **Image-size delta** — not a harness output. Measure directly:
   `docker images` (or `podman images`) before vs after the `llama-index-core` +
   `flashrank` deps land. Decision record `docs/decisions/0001-hierarchical-rerank-dependencies.md`
   estimated ~33 MB site-packages; confirm the built-image delta.

## Sweeps / open knobs

- **Parent/child chunk sizes are now configurable.** Set
  `data_manager.chunking.parent_chunk_size` / `child_chunk_size` (defaults 2048/512).
  To recommend sizes from data, clone the treatment config into variants differing
  **only** in those keys (e.g. 1024/256, 2048/512, 4096/512) and run each as its own
  pass (chunk size changes ingestion → same two-run protocol).
- **`bm25_weight` sweep** — clone the configs varying only
  `retrievers.hybrid_retriever.bm25_weight`, one pass each.
- **SOURCES mode is off.** The in-repo `fasrc_ragas_queries.json` is RAGAS-only; its
  zero-source `should_refuse` rows carry an empty `source_match_field`. For a
  headline run against real tickets, drop the operator-local
  `snow_ragas_queries_pt1.json` (gitignored real ServiceNow data) on disk and point
  both arms' `queries_path` at it. To add SOURCES scoring, use a url-bearing bank and
  reconcile its URLs against the ingested sitemap slugs first.
- **Tool-call cap interaction** — the agent's `search_vectorstore_hybrid` tool
  (`config/agents/fasrc-cannon.md`) can fire multiple times; capping it changes the
  candidate pool the reranker sees. Note the cap setting alongside results.
