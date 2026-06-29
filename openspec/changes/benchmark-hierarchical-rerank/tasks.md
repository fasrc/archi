## 1. Question banks (drafted this session)

- [x] 1.1 Add `examples/benchmarking/snow_ragas_queries_pt1.json` (27 real ServiceNow tickets, harness `question`/`answer` schema, RAGAS-only)
- [x] 1.2 Add `examples/benchmarking/fasrc_ragas_queries.json` (21 doc-grounded, typed `easy_retrieve`/`reasoning`/`should_refuse`) + README
- [ ] 1.3 Operator confirms the `DRAFT` ground-truth answers before the scored run (live docs already drifted, e.g. `--gpus=1`, `/n/holylabs`); remove `DRAFT` notes once locked
- [ ] 1.4 (optional) Reconcile `fasrc_ragas_queries.json` source URLs against ingested sitemap slugs if SOURCES mode is ever enabled

## 2. A/B config pair (drafted this session)

- [x] 2.1 Add `examples/benchmarking/hierarchical_rerank_ab/baseline_character_hybrid.yaml`
- [x] 2.2 Add `examples/benchmarking/hierarchical_rerank_ab/treatment_hierarchical_rerank.yaml`
- [x] 2.3 Add the A/B README (contract, run command, latency/image protocols, caveats)
- [x] 2.4 Verify minimal-diff: arms differ only in chunking/retriever/name/DATA_PATH
- [ ] 2.5 Point `input_lists` at the live dev corpus list (currently `config/lists/sources.list`) before the deploy run

## 3. Chunk-size config plumbing (code — TDD, gate-verifiable)

- [x] 3.1 Write failing unit tests: `data_manager.chunking.parent_chunk_size`/`child_chunk_size` drive `build_hierarchical_nodes`, and absence falls back to the existing defaults (2048/512)
- [x] 3.2 Read the two keys in the data-manager config path and pass them through the `manager.py` call site to `build_hierarchical_nodes` (via `_resolve_chunk_sizes` helper + `self.parent_chunk_size`/`self.child_chunk_size`)
- [x] 3.3 Confirm backward compatibility: omitting the keys reproduces current chunking (no behavior change) — `test_resolve_chunk_sizes_defaults_when_absent`
- [x] 3.4 Run `bash scripts/gate.sh` (format → lint → test; ≥80% diff coverage) — passed: 581 passed, diff coverage 83%

## 4. Docs

- [x] 4.1 Add the hierarchical-rerank A/B recipe to `docs/docs/benchmarking.md` (references the config dir + warm/cold latency + image-size protocols + the chunk-size sweep)
- [x] 4.2 Document the new `data_manager.chunking.parent_chunk_size`/`child_chunk_size` keys (new Chunking subsection in `docs/docs/configuration.md`)

## 5. Benchmark execution (needs-deploy — not gate-verifiable)

- [ ] 5.1 Build/refresh the benchmark images; record the built-image size with and without `llama-index-core` + `flashrank` (the image-size delta)
- [ ] 5.2 Run `archi evaluate -cd examples/benchmarking/hierarchical_rerank_ab` on the dev deployment (both arms ingest their own corpus); capture the dump JSON (`ab_comparisons` + `leaderboard`)
- [ ] 5.3 Re-run with the typed bank to obtain per-`anchor_type` slices
- [ ] 5.4 (if plumbing landed) Run a parent/child size sweep grid (e.g. 1024/256, 2048/512, 4096/512) via cloned configs differing only in the chunk-size keys
- [ ] 5.5 (optional) Run a `bm25_weight` sweep via cloned configs differing only in that weight

## 6. Analysis & recommendation

- [ ] 6.1 Compute warm latency per arm by excluding the treatment's first (model-load) query; report cold load separately
- [ ] 6.2 Summarize the quality delta overall and by question type (where does returning parent context help?)
- [ ] 6.3 Write a decision record under `docs/decisions/` with the recommendation (default-on/off, parent/child sizes, `bm25_weight`), each setting citing its measured number

## 7. Validate & archive

- [x] 7.1 `openspec validate benchmark-hierarchical-rerank --strict` — valid
- [ ] 7.2 Open a PR to `fasrc/archi:dev` with the code + data + docs changes (the deploy run + recommendation referenced in the PR body)
- [ ] 7.3 `/opsx:archive` once the recommendation lands and the change is merged
