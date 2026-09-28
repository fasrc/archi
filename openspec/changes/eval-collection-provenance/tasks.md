## 1. Retrieval identity helper (D1)

- [ ] 1.1 Write failing unit tests for `retrieval_identity(config)` in `tests/unit/test_benchmark_provenance.py`: HuggingFace `model_name`, OpenAI `model`, class with neither kwarg, collection formula equals `VectorstoreConnector`'s (`collection_name + "_with_" + embedding_name`), missing `data_manager` returns null identity
- [ ] 1.2 Implement `RetrievalIdentity` and `retrieval_identity()` in `src/utils/benchmark_provenance.py`; no imports of embedding classes, no connections
- [ ] 1.3 Add a unit test that instantiates `VectorstoreConnector` with a stubbed embedding class and asserts `connector.collection_name == retrieval_identity(config).collection`

## 2. Backfill of existing artifacts (D2)

- [ ] 2.1 Write failing unit tests for the backfill stamp: arm with `running_configuration` gets `retrieval_identity` with `source = "reconstructed from running_configuration"`; stamped arm is unchanged; arm without `running_configuration` is skipped and reported
- [ ] 2.2 Implement the stamp in `scripts/benchmarking/backfill_report_provenance.py`, additive, in the existing skip-if-stamped flow
- [ ] 2.3 Run `python scripts/benchmarking/backfill_report_provenance.py --dry-run` over `bench_out/banks/golden/results/` and record the count of stamped versus skipped arms in the PR body

## 3. Fingerprint v2 query and shared routine (D3, D5)

- [ ] 3.1 Write failing unit tests for `corpus_state_query(collection) -> (sql, params)` against a fixture database or a SQL-shape assertion: chunk rows use `LEFT JOIN documents`, the retrieval filter `(collection = %s OR collection IS NULL)`, `(d.id IS NULL OR d.is_deleted = FALSE)`; the chunk key falls back through `d.resource_hash`, `metadata resource_hash`, `metadata chunk_id`, `'id:' || c.id`; the chunk value hashes text, `collection` tag, and `embedding_model` tag; the chunk value includes the null-vector flag; parent rows require an in-scope child; doc rows require an in-scope chunk and hash `size_bytes`, `url`, `display_name`, `source_type`, and `extra_json->>'title'` only
- [ ] 3.2 Write failing behavior tests with an in-memory row model: ingest into another collection leaves the digest unchanged; a `collection` tag change moves it; orphan parents added or removed leave it unchanged; a `document_id IS NULL` chunk text change moves it; a `url`, `display_name`, `source_type`, or `title` change moves it; a `category`-only change does not; a vector set to `NULL` moves it; the digest starts with `sha256/v2:`
- [ ] 3.3 Implement `corpus_state_query()`, `live_corpus_fingerprint(pool, config=None)`, and a `version` argument on `corpus_fingerprint()` in `src/utils/benchmark_provenance.py`
- [ ] 3.4 Replace `CORPUS_STATE_QUERY` and `get_corpus_fingerprint`'s body in `src/bin/service_benchmark.py` with a call to `live_corpus_fingerprint(pool, running_config)`; keep the never-raises contract and the unavailable marker
- [ ] 3.5 Replace `_harness_query("CORPUS_STATE_QUERY")` in `scripts/benchmarking/category_census.py` with `live_corpus_fingerprint`; delete `_harness_query` if nothing else uses it
- [ ] 3.6 Rewrite `fm_fingerprint` in `scripts/benchmarking/feature_matrix/lib.sh` to import and call `live_corpus_fingerprint`; keep an import-failure message that names the routine and says to rebuild the stack from the campaign SHA
- [ ] 3.7 Extend `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` so the pin check and the harness fixture produce one equal `sha256/v2:` digest, and the import-failure path prints the rebuild message

## 4. Chunk embedding-model tag at ingest (D4)

- [ ] 4.1 Write failing unit tests in the data-manager test module: flat write site sets `metadata["embedding_model"]`; hierarchical write site sets it on children and parents; a class with no model kwarg yields the class name
- [ ] 4.2 Implement the tag at `manager.py` flat site (`entry_metadata`) and hierarchical site (`base_metadata`) through `retrieval_identity(self._data_manager_config-bearing config).embedding_model`
- [ ] 4.3 Write failing unit tests for `PostgresVectorStore`: `embedding_model` constructor argument; default derived from the embedding function's `model_name` or `model` attribute, else its class name; `add_texts()` writes `metadata["embedding_model"]` next to `collection`
- [ ] 4.4 Implement the constructor argument and the `add_texts()` tag in `postgres_vectorstore.py`; pass `retrieval_identity(...).embedding_model` from both construction sites (`vectorstore_connector.py:64`, `manager.py:248`)

## 5. Start guard and identity recording in the harness (D6, D1)

- [ ] 5.1 Write failing unit tests for `collection_readiness(pool, identity)`: zero chunks raises with collection and embedding name in the message; rows present but zero non-null vectors raises with both counts in the message; a differing tag raises with both values; all-null tags return `embedding_model_source = "config (chunks untagged)"` and log a warning; matching tags return `"chunks"`; the result carries `chunk_count`
- [ ] 5.2 Implement `collection_readiness()` in `src/utils/benchmark_provenance.py` (one `SELECT count(*), count(embedding), array_agg(DISTINCT metadata->>'embedding_model')` with the retrieval filter); the result carries `chunk_count` and `usable_chunk_count`
- [ ] 5.3 Call the guard in `service_benchmark.py` after `archi()` is built and before `corpus_before` is taken; write `retrieval_identity` (with `chunk_count` and `embedding_model_source`) into the per-arm record next to `corpus_fingerprint`
- [ ] 5.4 Add a unit test that a harness arm record contains `retrieval_identity` with all six fields

## 6. QA workflow provenance (D8)

- [ ] 6.1 Write failing unit tests for the QA run start: with `search_vectorstore_hybrid` in the spec tools, `manifest.json` gets `retrieval_identity` and `corpus_fingerprint_before` and the guard runs; without it, both are null and no connection opens
- [ ] 6.2 Write failing unit tests for scoring end: `summary.provenance` gains `corpus_fingerprint`, `corpus_unchanged_at_endpoints`, and `retrieval_identity`
- [ ] 6.3 Implement in `src/evaluation/qa/workflow.py` (run start near `LazyVectorstore(config)`, scoring end in the `summary["provenance"]` block) and `runtime.py` as needed; reuse `live_corpus_fingerprint` and `collection_readiness`

## 7. Comparison gates (D7)

- [ ] 7.1 Write failing unit tests in the `compare_runs` test module: `sha256:` versus `sha256/v2:` is refused with a version reason before G3; unequal fingerprints with unequal `collection` name both collections in the G3 reason; arms that differ only in `embedding_model` compare and the header states the varied factor; a missing `retrieval_identity` on one arm compares with a note
- [ ] 7.2 Extend `Arm` in `scripts/benchmarking/compare_runs.py` with `retrieval_identity`; add `fingerprint_version_gate()` ahead of `corpus_gate()`; extend the G3 reason; add the varied-factor header and the unrecorded note
- [ ] 7.3 Confirm `archive_run.sh` and `sweep_tools.py` treat the digest as an opaque string (no `sha256:` prefix parse); add a test if either parses it

## 8. Docstrings and docs (D9)

- [ ] 8.1 Correct the `corpus_fingerprint` docstring (`benchmark_provenance.py`) and the `get_corpus_fingerprint` docstring (`service_benchmark.py`): the embedding identity comes from the chunk tag and `embedding_model`, not from `embedding_name`
- [ ] 8.2 Update the fingerprint paragraph in `scripts/benchmarking/README.md` (line ~133): one routine, v2 scope, re-pin on deploy
- [ ] 8.3 Update `docs/docs/proposals/multi-collection-routing.md`: "minimum change 1" says embedding model (`model_name`); add the order "17 (backfill first), 19, 18, then re-pin" and the #411 note; `mkdocs build --strict` passes
- [ ] 8.4 Comment on #570 with the change name and the revised plan; comment on #411 that fingerprint v2 is immune to its cleanup

## 9. Gate and hand-off

- [ ] 9.1 `bash scripts/gate.sh` passes (black, isort, `pytest tests/unit/`, diff-cover ≥ 80% versus `origin/dev`)
- [ ] 9.2 `bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` passes
- [ ] 9.3 Open the PR to `fasrc/archi:dev` with `Closes #570`; the PR body lists the migration order from `design.md` (merge → backfill → redeploy → re-pin → campaign) and the dry-run counts from 2.3
