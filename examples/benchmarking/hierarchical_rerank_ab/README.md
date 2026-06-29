# Hierarchical-rerank A/B (issue #32, task 6.2)

A two-arm benchmark measuring the #31 hierarchical-rerank retriever against the
pre-#31 baseline, on the FASRC corpus. The arms differ in **one** dimension:

| | Chunking | Retriever | Returns |
|---|---|---|---|
| `baseline_character_hybrid.yaml`  | `character` (CharacterTextSplitter) | `HybridRetriever` | child chunks |
| `treatment_hierarchical_rerank.yaml` | `sentence` (parent/child structural) | `LlamaIndexHierarchicalRetriever` (FlashRank rerank) | parent context |

Everything else — embedding model, BM25/semantic weights, SUT model, RAGAS judge,
question bank — is held identical. The harness records the shared values in
`leaderboard.shared_context` and warns if any drift, so the comparison stays
apples-to-apples.

## Run it

```bash
mkdir -p bench_out/hierarchical_rerank_ab
archi evaluate -n hr-ab -cd examples/benchmarking/hierarchical_rerank_ab \
  -e ~/.archi/.env.benchmark --hostmode
```

Because 2+ configs run, the dump JSON gains both `ab_comparisons` (pairwise,
including `time_a`/`time_b`) and a `leaderboard` (ranked by `faithfulness`).

Prereqs (this is a `needs-deploy` task — the local gate cannot run it):
- FASRC VPN up (the vLLM SUT + HuggingFace embeddings need split-DNS).
- `HUIT_API_KEY` in `~/.archi/.env.benchmark` for the Bedrock judge.
- Each arm ingests its **own** vectorstore under its `global.DATA_PATH` — the two
  must stay distinct (baseline char-split vs treatment hierarchical).

## What #32 wants out of this

1. **Quality** — the four RAGAS metrics per arm from the leaderboard. Watch the
   `reasoning` questions especially: returning parent context should help
   synthesis more than single-fact lookups (use `fasrc_ragas_queries.json` to
   slice by `anchor_type`).
2. **Latency** — `ab_comparisons` reports per-question `time_a`/`time_b`. The
   treatment's **first** query pays a one-time FlashRank ONNX model load (~45s on
   dev vs ~8s baseline). To report *warm* latency, discard the first treatment
   question (or prepend a throwaway warmup question to the bank) and average the rest.
3. **Image-size delta** — not a harness output. Measure directly:
   `docker images` (or `podman images`) before vs after the `llama-index-core` +
   `flashrank` deps land. Decision record `docs/decisions/0001-hierarchical-rerank-dependencies.md`
   estimated ~33 MB site-packages; confirm the built-image delta.

## Known limitations / open knobs

- **Parent/child sizes are not swept here.** `parent_chunk_size`/`child_chunk_size`
  are hardcoded (2048/512 in `node_parsing.py`) and not read from config, so this
  pair benchmarks the treatment *at its defaults only*. A data-driven size
  recommendation needs those plumbed through `data_manager.chunking` first.
- **`bm25_weight` is sweepable today** — to tune it, clone this dir into N configs
  varying only `retrievers.hybrid_retriever.bm25_weight` and run with `-cd`.
- **SOURCES mode is off.** The snow bank has no source URLs (operator answers).
  To add SOURCES scoring, use a url-bearing bank and reconcile its URLs against
  the ingested sitemap slugs first.
- **Tool-call cap interaction** — the agent's `search_vectorstore_hybrid` tool
  (`config/agents/fasrc-cannon.md`) can fire multiple times; capping it changes
  the candidate pool the reranker sees. Note the cap setting alongside results.
