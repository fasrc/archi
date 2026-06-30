## 1. Question banks (drafted this session)

- [x] 1.1 ServiceNow bank (`snow_ragas_queries_pt1.json`, 27 real tickets) — kept operator-local + gitignored (real ticket data); NOT committed. Drop on disk + repoint `queries_path` for the headline run
- [x] 1.2 Add `examples/benchmarking/fasrc_ragas_queries.json` (21 doc-grounded, typed `easy_retrieve`/`reasoning`/`should_refuse`) + README
- [x] 1.3 Operator confirms the `DRAFT` ground-truth answers before the scored run — **OUT OF SCOPE for this change**; externalized as a HIGH-priority operator task in Asana (p-Search-Engine-LLM, "Confirm benchmark ground-truth answers against current FASRC docs"). Live docs already drifted (e.g. `--gpus=1`, `/n/holylabs`); remove `DRAFT` notes once locked
- [ ] 1.4 (optional) Reconcile `fasrc_ragas_queries.json` source URLs against ingested sitemap slugs if SOURCES mode is ever enabled

## 2. A/B config pair (drafted this session)

- [x] 2.1 Add `examples/benchmarking/hierarchical_rerank_ab/baseline_character_hybrid.yaml`
- [x] 2.2 Add `examples/benchmarking/hierarchical_rerank_ab/treatment_hierarchical_rerank.yaml`
- [x] 2.3 Add the A/B README (contract, run command, latency/image protocols, caveats)
- [x] 2.4 Verify minimal-diff: arms differ only in chunking/retriever/name/DATA_PATH
- [x] 2.5 Point `input_lists` at the live dev corpus list (currently `config/lists/sources.list`) before the deploy run

## 3. Chunk-size config plumbing (code — TDD, gate-verifiable)

- [x] 3.1 Write failing unit tests: `data_manager.chunking.parent_chunk_size`/`child_chunk_size` drive `build_hierarchical_nodes`, and absence falls back to the existing defaults (2048/512)
- [x] 3.2 Read the two keys in the data-manager config path and pass them through the `manager.py` call site to `build_hierarchical_nodes` (via `_resolve_chunk_sizes` helper + `self.parent_chunk_size`/`self.child_chunk_size`)
- [x] 3.3 Confirm backward compatibility: omitting the keys reproduces current chunking (no behavior change) — `test_resolve_chunk_sizes_defaults_when_absent`
- [x] 3.4 Run `bash scripts/gate.sh` (format → lint → test; ≥80% diff coverage) — passed: 581 passed, diff coverage 83%
- [x] 3.5 Render `parent_chunk_size`/`child_chunk_size` in `src/cli/templates/base-config.yaml` so the keys actually reach the deployed runtime config (Codex #770; previously only `strategy` rendered) — `test_base_config_chunking_render.py`

## 3b. Review fixes (Codex, PR #72)

- [x] 3b.1 Two separate deploy+ingest+evaluate passes instead of one `-cd` run — the harness sweeps prompts over a single corpus and rejects differing `global` (Codex P1 #769/#771); design D1/D3, spec, configs, docs revised
- [x] 3b.2 Gate source match-field computation on SOURCES mode (`_resolve_reference_match_fields`) so RAGAS-only banks with zero-source `should_refuse` rows load (Codex #780); empty `source_match_field` on those rows — `test_benchmark_ragas_only_match_fields.py`
- [x] 3b.3 Add required `-n` to the docs A/B `evaluate` command (Codex #774)
- [x] 3b.4 ServiceNow bank removed from git (Codex #787) — see 1.1
- [x] 3b.5 Repoint `agent_md_file` at a checked-in FASRC persona so the configs validate from a clean checkout — new `examples/agents/fasrc-docs.md` placeholder (Codex re-review #3489134738); regression-guarded by `test_hierarchical_rerank_ab_configs.py`
- [x] 3b.6 Set `ragas_settings.embedding_model: huggingface` in both arms — the omitted key rendered the `OpenAI` default and failed scoring under HUIT-only creds (Codex re-review #3489134745)
- [x] 3b.7 Make the operator-supplied corpus a loud, REQUIRED prereq (both config comments + README verify-non-empty step) — missing `config/lists/sources.list` silently yields an empty corpus + meaningless RAGAS (Codex re-review #3489134741); kept FASRC corpus (no checked-in FASRC list exists; a foreign/empty corpus would be equally meaningless)
- [x] 3b.8 (operator) Stage the live FASRC corpus list (`config/lists/sources.list`) or repoint `input_lists` before the deploy run, and confirm `weblists/<list>` ingested a non-zero document count

## 4. Docs

- [x] 4.1 Add the hierarchical-rerank A/B recipe to `docs/docs/benchmarking.md` (references the config dir + warm/cold latency + image-size protocols + the chunk-size sweep)
- [x] 4.2 Document the new `data_manager.chunking.parent_chunk_size`/`child_chunk_size` keys (new Chunking subsection in `docs/docs/configuration.md`)

## 5. Benchmark execution (needs-deploy — not gate-verifiable)

- [x] 5.1 Build/refresh the benchmark images; record the built-image size with and without `llama-index-core` + `flashrank` (the image-size delta)
- [x] 5.2 Run each arm as its OWN deploy+ingest+evaluate pass on dev: `archi evaluate -n hr-ab-baseline -c baseline_character_hybrid.yaml`, then redeploy+re-ingest and `-n hr-ab-treatment -c treatment_hierarchical_rerank.yaml`; capture each run's dump JSON (per-arm RAGAS aggregate + per-question `time_elapsed`). NOT a single `-cd` run (design D1)
- [x] 5.3 Use the typed `fasrc_ragas_queries.json` bank for per-`anchor_type` slices; compare the two runs' aggregates offline
- [ ] 5.4 Run a parent/child size sweep (e.g. 1024/256, 2048/512, 4096/512) via configs differing only in the chunk-size keys — each as its own pass (chunk size changes ingestion)
- [ ] 5.5 (optional) Run a `bm25_weight` sweep via cloned configs differing only in that weight

## 6. Analysis & recommendation

- [x] 6.1 Compute warm latency per arm by excluding the treatment's first (model-load) query; report cold load separately
- [x] 6.2 Summarize the quality delta overall and by question type (where does returning parent context help?)
- [x] 6.3 Write a decision record under `docs/decisions/` with the recommendation (default-on/off, parent/child sizes, `bm25_weight`), each setting citing its measured number

## 7. Validate & archive

- [x] 7.1 `openspec validate benchmark-hierarchical-rerank --strict` — valid
- [ ] 7.2 Open a PR to `fasrc/archi:dev` with the code + data + docs changes (the deploy run + recommendation referenced in the PR body)
- [ ] 7.3 `/opsx:archive` once the recommendation lands and the change is merged
