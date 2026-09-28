## Why

The next eval campaign (golden-set era G, then the #216 embedding A/B) records nothing that names the searched collection or the model that produced the stored vectors, and the corpus fingerprint hashes rows that retrieval cannot reach. Two #216 arms with different embedding models get equal fingerprints, equal collection names, and no gate that tells them apart. The #411 orphan-parent cleanup moves the fingerprint while the live corpus stays the same. Every fix to the fingerprint forces a re-pin, so all of them must land before the campaign pin, in one deploy.

This change implements [#570](https://github.com/fasrc/archi/issues/570) as revised by the review of PR #569.

## What Changes

- **Retrieval identity on every run.** One helper derives `collection`, `embedding_name`, and `embedding_model` from a config dict, with the same formula as `VectorstoreConnector` (`vectorstore_connector.py:36`). The golden-set harness and the QA workflow record it, and the sweep ledger rows (`archive_run.sh`, `qa_arm.sh`, `sweep_tools.py`) carry `collection` and `embedding_model`.
- **Backfill of existing artifacts.** Arms that carry `running_configuration` (every dev report since #272) get the identity stamped from it, per arm, independent of the script's file-level "already stamped" skip. The stamp is additive and labelled as reconstructed. The era-F and era-G baselines then carry the fields with no re-run.
- **Corpus fingerprint v2.** The fingerprint is scoped to the searched collection, starts from every chunk that retrieval can return (chunks with no document link included), hashes each chunk's `collection` tag and null-vector flag, hashes the document fields retrieval returns with each chunk (URL, display name, source type, title, each nullable field coalesced), and hashes a parent node only when a chunk references it. The digest is model-neutral: the #216 arms share one digest and differ in the recorded identity. The category-map digest gets the same collection scope. The digest carries the prefix `sha256/v2:`. **BREAKING for pins:** a v1 pin never equals a v2 fingerprint. `compare_runs.py` refuses a v1/v2 mix with a version reason, not a corpus reason. A re-pin is required after deploy.
- **One fingerprint routine.** The harness, the QA workflow, `feature_matrix/lib.sh`, and `category_census.py` call one function in `src/utils/benchmark_provenance.py`, with the config as a required argument. The AST read of `CORPUS_STATE_QUERY` and `CATEGORY_MAP_QUERY` from the harness source goes away. The `lib.sh` snippet installs the Postgres factory it builds before it reads the config.
- **Chunks carry the embedding model.** The data manager writes `metadata["embedding_model"]` at both of its chunk write sites (flat and hierarchical), and `PostgresVectorStore.add_texts()` writes it for the direct write path.
- **Start guard.** A run refuses to start when the searched collection has zero chunks, zero chunks with a vector, or when any chunk's `embedding_model` tag differs from the running query model. Rows without a tag (legacy) are recorded as unverified with a warning, not refused; a partially tagged collection is recorded as unverified with the untagged count.
- **QA workflow provenance.** A QA run records the fingerprint at run start and when its attempts finish, plus the identity, in `manifest.json`; scoring copies them into `summary.provenance`. The retry path does the same. Today it records none of this.
- **Comparison gates.** Unequal fingerprint versions are refused with their own reason. An unequal collection is named in the G3 reason. An unequal `embedding_model` is allowed and labelled as the varied factor. An unrecorded identity is allowed with a note (unknowable, not unequal).
- **Docstrings.** `corpus_fingerprint` and `get_corpus_fingerprint` stop the claim that a re-embed shows as an `embedding_name` divergence. It does not: #216 changes only `model_name`.
- **Docs.** The fingerprint section of `scripts/benchmarking/README.md`, and the eval section of `docs/docs/proposals/multi-collection-routing.md` (say "embedding model", give the tasks an order).

## Capabilities

### New Capabilities
- `eval-corpus-provenance`: what an evaluation run records about the corpus it searched (collection, embedding model, collection-scoped fingerprint), the start guard, the shared fingerprint routine, the comparison gates, and the backfill.

### Modified Capabilities
- `ingest-processing`: ADDED requirement. Each chunk the data manager writes carries the identity of the model that embedded it.
- `qa-evaluation-trial`: ADDED requirement. A QA run records corpus provenance, not only the config, spec, and profile digests.

## Impact

- **Code:** `src/utils/benchmark_provenance.py` (identity helper, fingerprint v2 query and routine), `src/bin/service_benchmark.py` (record identity, start guard, use the routine), `src/evaluation/qa/workflow.py` and `runtime.py` (record identity and fingerprint, start guard), `src/data_manager/vectorstore/manager.py` (chunk tag at lines 629-632 and 911-913), `src/data_manager/vectorstore/postgres_vectorstore.py` (`embedding_model` constructor argument, tag in `add_texts()`), `scripts/benchmarking/compare_runs.py` (gates), `scripts/benchmarking/backfill_report_provenance.py` (identity stamp), `scripts/benchmarking/feature_matrix/lib.sh` and `category_census.py` (call the routines), `feature_matrix/archive_run.sh`, `qa_arm.sh`, and `sweep_tools.py` (ledger identity fields, version-aware fingerprint predicate).
- **Tests:** unit tests for the helper, the v2 queries, the guard, the gates, and the backfill; `test_feature_matrix_wrappers.sh` for `lib.sh`, `archive_run.sh`, and `qa_arm.sh`.
- **Validation:** one end-to-end check on the claw stack before the implementation PR opens (AGENTS.md, "Deployment & Validation Policy").
- **Deploy:** the dev host runs harness commit `2e117ee0` (2026-09-16), 80 commits behind `origin/dev`. New recording reaches an artifact only after `redeploy.sh`. The redeploy re-ingests, which populates the chunk tags, and it invalidates the v1 pin. Order that costs one pin: merge → redeploy → re-pin (v2) → campaign.
- **Issues:** #570 (implemented here), #216 (first consumer of the `embedding_model` gate), #411 (v2 is immune to its cleanup; v1 pins are not), #119 (a crash in the embed stage leaves a partial corpus; the v2 pin and the zero-chunk guard both catch it).
- **No change** to retrieval, to the chat app, or to the `documents` schema.
