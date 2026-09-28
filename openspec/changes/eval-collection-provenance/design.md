## Context

Line anchors are against `origin/dev` at `150dbff4` (2026-09-28; #571 added gate G10 to `compare_runs.py`). Re-derive them with `git show origin/dev:<path> | grep -n` before an edit.

**How a run finds its collection.** `VectorstoreConnector` computes `collection_name + "_with_" + embedding_name` one time (`src/archi/utils/vectorstore_connector.py:36`). The golden-set harness builds `archi()` (`src/bin/service_benchmark.py:1527`), so its collection comes from the running config in Postgres. The QA workflow passes its in-memory resolved config to `LazyVectorstore` (`src/evaluation/qa/workflow.py:364-370`, `runtime.py:322-339`). Neither records the collection or the embedding model as a field.

**What the artifacts hold today.** Every dev report since #272 has, per arm, `running_configuration`, `corpus_fingerprint`, `corpus_fingerprint_before`, `corpus_unchanged_at_endpoints`, and `configuration_divergence` (checked in `bench_out/banks/golden/results/benchmarking-ragas-devbench-20260920_073251.json`). From `running_configuration`: `collection_name = default_collection`, `embedding_name = HuggingFaceEmbeddings`, `model_name = sentence-transformers/all-MiniLM-L6-v2`. The QA workflow records three digests only: agent config, agent spec, evaluator profile (`workflow.py:852-860`, `scoring.py:281-283`).

**What the fingerprint hashes.** `CORPUS_STATE_QUERY` (`service_benchmark.py:110`) hashes three row kinds over the whole database: `doc` (live documents, `size_bytes`), `chunk` (inner join to `documents`, `md5(chunk_text)`), `parent` (all parent nodes of live documents, with the child list). It has four gaps:

1. No collection filter, and the `collection` tag is not hashed.
2. Inner join to `documents`, but retrieval uses `LEFT JOIN` (`postgres_vectorstore.py:377`, `:509`) and returns chunks with `document_id IS NULL`. The data manager writes such chunks (`manager.py:702`, `:781`).
3. Parent nodes that no chunk references are hashed. Retrieval reaches a parent only through `chunk.metadata.parent_id`. Same-text duplicates collapse in the `GROUP BY`, but orphans with no live twin do not.
4. Nothing identifies the embedding model. Chunk metadata holds `chunk_index`, `filename`, `resource_hash`, `collection` (`manager.py:629-632`, `911-913`). The `corpus_fingerprint` docstring says a re-embed shows as an `embedding_name` divergence (`benchmark_provenance.py:307-309`). #216 changes only `model_name`, so it does not.

**Measured state.** Claw (`postgres-claw`, 2026-09-28): 841 live documents, 6,181 chunks, all tagged `default_collection_with_HuggingFaceEmbeddings`, 0 with `document_id IS NULL`; 13,822 parent nodes, 1,992 referenced, 11,830 orphans, 45 orphans with no live twin (same document, index, and text). Dev (#411, 2026-09-16): 27,457 orphans, 92% of the table.

**Who else reads the query.** `feature_matrix/lib.sh` (`fm_fingerprint`) and `category_census.py` (`_harness_query`) read the `CORPUS_STATE_QUERY` constant out of the harness source by AST and execute it with no parameters. The chunker uses llama_index's `get_tokenizer()` (`node_parsing.py:329`), not the embedding model, so a re-embed leaves chunk text unchanged.

**Consumers.** `compare_runs.py` gate G3 (`corpus_gate`) refuses unequal or unrecorded fingerprints; an absent `corpus_unchanged_at_endpoints` is treated as unknowable and allowed. The dev host runs harness commit `2e117ee0` (2026-09-16), 80 commits behind `origin/dev`.

## Goals / Non-Goals

**Goals:**
- Every run and every arm names the collection it searched and the model that produced the vectors.
- The fingerprint equals exactly when the retrievable state of the searched collection is equal, and for no other reason.
- One routine computes the fingerprint for every consumer.
- A run against an empty or mismatched collection stops before its first question.
- The #216 arms are comparable, and the report names the embedding model as the varied factor.
- Existing baselines gain the identity fields without a re-run.

**Non-Goals:**
- Multi-collection routing, collection groups, the `documents` unique key, and auth (the rest of the multi-collection proposal).
- A fix for #411 itself (the fingerprint becomes immune to it; the cleanup stays #411's work).
- Hashing embedding vectors (see D3).

## Decisions

### D1. One identity helper, derived from config

`retrieval_identity(config) -> RetrievalIdentity` in `src/utils/benchmark_provenance.py` returns `collection` (the same formula as `vectorstore_connector.py:36`), `embedding_name`, and `embedding_model`. `embedding_model` is `kwargs["model_name"]` for `HuggingFaceEmbeddings` and `kwargs["model"]` for `OpenAIEmbeddings` (`base-config.yaml:260`, `:265`); when neither key exists, it is the class name. The helper never imports the embedding class or opens a connection, so the backfill and the QA path can call it on a plain dict. The sweep ledger rows (`archive_run.sh`, `qa_arm.sh`, `sweep_tools.py`) copy `collection` and `embedding_model` from the artifact or the run manifest, so a campaign ledger tells #216 arms apart without a read of each artifact.

*Alternative rejected:* read the identity from a live `VectorstoreConnector`. The QA path builds the connector lazily and only when the spec uses the search tool, and the backfill has no connector at all.

### D2. Backfill first

`backfill_report_provenance.py` gains one stamp: when an arm has `running_configuration`, write `retrieval_identity` with `"source": "reconstructed from running_configuration"`. The stamp is independent of the script's file-level skip. Today `stamp_file()` returns `skipped (already stamped)` when the metadata has any of `code_version`, `config_version`, `config_versions` (`backfill_report_provenance.py:70`, `:99`), before it looks at an arm. The era-F and era-G reports carry those keys, so that skip would pass over exactly the arms this stamp is for. The identity pass iterates every arm of every report and stamps the arms that lack `retrieval_identity`; the version keys stay as they are. Existing keys are never overwritten. This follows the script's own rule: stamp what the artifact proves, invent nothing. Arms without `running_configuration` (before #272) get no identity.

### D3. Fingerprint v2 hashes what retrieval can reach, and only that

The v2 query takes one parameter, the collection tag, and returns the same `(key, value)` rows for `corpus_fingerprint`:

- **Encoding.** Every row value is `md5(jsonb_build_array(f1, f2, ...)::text)`. A JSON array keeps field boundaries (`["a|b","c"]` and `["a","b|c"]` differ), keeps `null` distinct from `""`, and needs no sentinel. Bare `||` with `|` delimiters was rejected: `|` is legal in chunk text, names, URLs, and titles, and `||` with a `NULL` operand makes the whole value `NULL` (`url` and `size_bytes` are nullable, `init.sql:219`, `:226`).
- **Citation fields.** One fixed list of the metadata keys that reach the agent and the citations through `_merge_row_metadata` (`postgres_vectorstore.py:23-52`): `url`, `display_name`, `source_type`, `title`, `filename`. For a chunk with a document row, `_merge_row_metadata` overlays the document's columns (`postgres_vectorstore.py:367-375`, `:493-512`), so the `doc` row carries them. Every chunk and every parent node also carries them in its own `metadata`, which the agent sees wherever the document column is empty, and `filename` is only there (`hierarchical_retriever.py:162-193`), so those rows carry them. The whole `metadata` object is never hashed: `parent_id` is a `SERIAL` that changes on every ingest, `ingested_at` and `ingestion_status` churn, `embedding_model` must stay out (model-neutral), and `category` and `llm_category` belong to the category-map digest.
- `chunk` rows: `FROM document_chunks c LEFT JOIN documents d ON d.id = c.document_id`, with the retrieval filter `(c.metadata->>'collection' = %s OR c.metadata->>'collection' IS NULL)` and `(d.id IS NULL OR d.is_deleted = FALSE)`. Key: `chunk:<ref>:<chunk_index>` where `<ref>` is `COALESCE(d.resource_hash, c.metadata->>'resource_hash', c.metadata->>'chunk_id', 'id:' || c.id)`. Value fields: `chunk_text`, the `collection` tag, `(c.embedding IS NULL)`, and the five citation fields from `c.metadata` for every chunk (`filename` exists only there, and `_merge_row_metadata` keeps the chunk's own `url` and `title` wherever the document column is empty). The `embedding_model` tag is not part of the value; see "model-neutral" below. The null-vector flag is part of the value because the schema allows a `NULL` embedding (`init.sql:275`) and semantic search still returns such a row when fewer than `k` rows have a vector (`ORDER BY distance ASC` sorts `NULL` last, `postgres_vectorstore.py:378-380`).
- `parent` rows: only parents with at least one in-scope child (`INNER JOIN` through `parent_id`). Key as in v1. Value fields: `parent_text`, the ordered child index list, and the five citation fields from `p.metadata`. The hierarchical retriever returns `p.parent_text` and `p.metadata` merged with the document columns (`hierarchical_retriever.py:162-193`), so a parent's metadata is what the agent sees.
- `doc` rows: only live documents with at least one in-scope chunk. Value fields: `url`, `display_name`, `source_type`, `extra_json->>'title'`. `size_bytes` is out: neither retrieval query selects it (`postgres_vectorstore.py:367-375`, `:493-512`), so a size-only change (stripped markup, PDF metadata, whitespace) has no retrieval effect and must not move the digest; the chunk rows catch every text change. The rest of `extra_json` stays out on purpose: `category` lives there, and #524/#538 decided that a category-only change moves the per-arm category-map digest, not the corpus fingerprint.
- `category_map_query(collection)`: the URL → category map (`CATEGORY_MAP_QUERY`, `service_benchmark.py:139-143`) gets the same scope: documents with at least one in-scope chunk and `url IS NOT NULL`. Today it reads every live document, so a relabel in another collection moves the per-arm category digest of a run that never searched it.
- The digest is `sha256/v2:<hex>`. `corpus_fingerprint()` itself is unchanged; the caller supplies the prefix through a `version` argument.

Effects: an ingest into another collection does not move the digest. A move of chunks between collections does. The #411 cleanup does not. A failed document with no chunks drops out (retrieval never sees it). A chunk written through `add_texts()` with no document link is included. A change to a document's URL, display name, source type, or title moves the digest; a category-only change does not. A re-embed of identical text with another model does not move the digest; the identity records the model. A size-only change does not move it. A change to the citation metadata of a documentless chunk or of a parent node does.

**Model-neutral on purpose.** The #216 arms re-embed identical text with models A and B. If the `embedding_model` tag were part of the chunk value, every chunk value would differ, G3 would refuse the pair, and an override flag would hide any text or metadata drift that happened at the same time. The model identity travels beside the digest instead: `retrieval_identity.embedding_model`, verified against the chunk tags by the guard (D6). Equal digests plus different identities is the exact state a valid embedding A/B is in.

*Alternative rejected:* hash the embedding vectors. Float output can differ across hardware and kernels for one model, so two identical ingests would get different digests. The per-chunk `embedding_model` tag (D4) carries the model identity instead.

*Alternative rejected:* keep a parameterless query that reads the collection from the config tables in SQL. It duplicates the `_with_` formula in SQL and binds the fingerprint to the config schema.

*Alternative rejected:* hash the whole `extra_json`. That reverses the #524/#538 decision and makes every category re-label look like a corpus change.

### D4. Every chunk write site tags the chunk with its embedding model

The data manager's two write sites (`manager.py:629-632` flat, `:911-913` hierarchical) add `metadata["embedding_model"] = <D1 embedding_model of the data-manager config>`. Parents get the tag through `base_metadata` for free. These sites insert with their own SQL and never call `add_texts()`.

`PostgresVectorStore.add_texts()` is the third write site. No ingest code calls it today (only `add_documents()` and `from_texts()` inside the class), but it is public API and D3 includes its chunks, so it tags them too. `PostgresVectorStore.__init__` gains `embedding_model: Optional[str] = None`; when `None`, it reads `model_name` or `model` off the embedding function, else the class name. `add_texts()` writes `meta["embedding_model"]` next to `meta["collection"]` (`postgres_vectorstore.py:185`). Both construction sites pass D1's value (`vectorstore_connector.py:64`, `manager.py:248`).

The tag needs a re-ingest to appear; the deploy that ships this change re-ingests (`reset_collection` defaults to true, `base-config.yaml:316`).

### D5. One routine, no AST

Two layers in `benchmark_provenance.py`. The row layer runs on a caller's cursor: `corpus_state_rows(cursor, collection) -> rows` and `category_map_rows(cursor, collection) -> list[tuple[url, category]]`. The convenience layer opens its own connection: `live_corpus_fingerprint(pool, config) -> str` and `live_category_map(pool, config) -> (rows, records, digest)`; both derive the collection with D1 from the given `config`. The census keeps its `REPEATABLE READ` read-only transaction (`category_census.py:270-275`) and calls the two row functions on that one cursor, so its map digest and its fingerprint still describe one database state, and `coverage_census`, `vocabulary_census`, and `bank_census` keep their `(url, category)` tuples (`category_census.py:93-153`). The harness reads the two at each endpoint through the convenience layer, as it does today. `config` is a required argument, not a default: `get_full_config()` reads through `PostgresServiceFactory.get_instance()` and raises `ConfigNotReadyError` when no factory is installed (`config_access.py:15-20`), and the `lib.sh` snippet builds its pool with `PostgresServiceFactory.from_env()` without an install (`lib.sh:96-97`). The harness's `get_corpus_fingerprint` and `get_category_map` call the routines with the running config. The QA workflow calls them with its resolved config. `lib.sh`'s container snippet installs the factory it builds (`PostgresServiceFactory.set_instance(factory)`), reads `get_full_config()`, and calls the routine; it keeps its "this image predates ..." error when the import fails, as today. `category_census.py` calls the two row functions on its own cursor and drops `_harness_query`. Drift between consumers becomes impossible by construction, which is what the AST read tried to give.

### D6. Start guard in both eval paths

Before the first question, the harness (after `archi()` is built) and the QA workflow (only when `search_vectorstore_hybrid` is in `spec.tools`) run one check:

1. `SELECT count(*), count(embedding), count(*) FILTER (WHERE metadata->>'embedding_model' IS NULL), array_agg(DISTINCT metadata->>'embedding_model') FILTER (WHERE metadata->>'embedding_model' IS NOT NULL)` for the searched collection (with the retrieval filter). The four values are `chunk_count`, `usable_chunk_count`, `untagged_chunk_count`, and the set of tags.
2. Zero chunks, or zero chunks with a vector: raise with the collection, the embedding name, and both counts in the message. This is fatal before scoring; there are no scores to lose. A collection whose rows all have a `NULL` vector has no semantic result to give.
3. Any tag that differs from the running `embedding_model`: raise, with both values in the message.
4. `embedding_model_source`: every chunk untagged (legacy rows) → `"config (chunks untagged)"` with a warning; some chunks untagged and every tag equal to the running model → `"chunks (N untagged)"` with a warning, because N vectors have no recorded model; every chunk tagged and equal → `"chunks"`. The untagged count is a direct count. (`array_agg` keeps `NULL` elements on Postgres, verified on `postgres-claw`, but a count makes the mixed state a recorded number rather than an array inspection.)

The check records `chunk_count`, `usable_chunk_count`, and `untagged_chunk_count` with the identity. The harness's existing ingest wait (`_ingest_is_progressing`) stays; it watches the endpoint state, not the collection.

### D7. Gates in `compare_runs.py`

- **Version:** if the arms' fingerprint prefixes differ, refuse with "fingerprint versions differ; re-pin and re-run". This check runs before the G3 equality check, so a v1/v2 mix is never reported as "different corpora". The same gate runs over the baseline and every noise replicate before `check_noise_replicates()`'s own corpus check (`compare_runs.py:949`, its G3 refusals at `:1003-1019`), which otherwise reports a v1/v2 mix as a generic corpus mismatch that `--corpus-differs-by-design` can wave through.
- **G3 reason:** when fingerprints differ and the recorded `collection` values differ, the reason names both collections.
- **Embedding:** when the fingerprints are equal and `embedding_model` differs, the comparison runs and the report header states "varied factor: embedding_model A → B". #216 needs exactly this. The digest is model-neutral (D3), so equal digests prove that the text, the collection, and the document fields were the same, and the identities prove which model each arm used. The label requires verified provenance on both arms: `embedding_model_source == "chunks"` and `untagged_chunk_count == 0`. When either arm's vectors are unverified (`"config (chunks untagged)"`, `"chunks (N untagged)"`, or no identity at all), the comparison is refused with "embedding provenance unverified for <arm>", and no flag overrides it: two runs over an untagged legacy collection can both have been served by one older model, whatever their configs say.
- **Unrecorded identity:** allowed with a note, as `corpus_unchanged_at_endpoints` is today (absent is unknowable, not unequal).

### D8. QA workflow provenance

Retrieval happens in `run()` and in `retry()`. `score()` can run hours later and retrieves nothing. So the two fingerprint readings bracket the answering phase, not the whole workflow:

- At run start, after the guard (D6), `run()` records `retrieval_identity` and `corpus_fingerprint_before` in `manifest.json` next to `agent` (`workflow.py:364-380`).
- When the attempts finish, before `manifest["status"] = "run_completed"` (`workflow.py:587-597`), `run()` records `corpus_fingerprint` and `corpus_unchanged_at_endpoints` in the manifest.
- `retry()` builds a fresh vectorstore and executes new attempts (`workflow.py:941-949`) and writes its own manifest (`:1102-1111`). It runs the same guard and takes the same two readings.
- `score()` (`:862`) and the retry scoring path (`:1187`) copy the manifest's readings and identity into `summary.provenance`. They take no new reading. A corpus change between answering and scoring is not a change the answers saw, and a change during answering that was reverted before scoring must still show.

The sweep's outer `live-stack-equals-pin` check stays as is.

### D9. Docstrings and docs

`corpus_fingerprint` (`benchmark_provenance.py:307-309`) and `get_corpus_fingerprint` (`service_benchmark.py:369-372`) state that the embedding identity comes from the per-chunk tag and the recorded `embedding_model`, not from `embedding_name`. `scripts/benchmarking/README.md:133` describes the routine, not the AST read. The proposal's eval section says "embedding model (`model_name`)" and gives tasks 17-19 their order.

## Risks / Trade-offs

- [Every v1 pin becomes stale on deploy] → The version gate refuses a v1/v2 mix with a clear reason. The campaign re-pins once, after the deploy. The v1 artifacts stay valid among themselves.
- [The `lib.sh` snippet runs inside the data-manager container, which must carry the new routine] → Keep the existing failure message pattern: an import error names the missing routine and tells the operator to rebuild the stack from the campaign SHA. `test_feature_matrix_wrappers.sh` covers the snippet.
- [The `sha256/v2:` prefix fails `archive_run.sh`'s usable-fingerprint test, `x.startswith("sha256:")` (`archive_run.sh:174`), so no v2 artifact could set the new pin] → That predicate becomes version-aware: it accepts both prefixes and refuses a pin/artifact version mix. The other readers (`category_slice._usable`, `sweep_tools._usable`, `compare_runs._recorded`) test only for the unavailable marker and accept v2 as they are; each gets a test.
- [The `doc` row semantics change: a failed document drops out] → Intended. A document with no chunks in scope is invisible to retrieval. The change is part of the v2 version bump, not a silent shift.
- [`add_texts()` has no ingest caller, so its tag has no production test path] → Unit tests cover it directly; the tag costs one dict write. Leaving it out would make D3's "every retrievable chunk" claim false for a public write path.
- [Legacy rows have no `embedding_model` tag until re-ingest] → The guard records "config (chunks untagged)" and warns, it does not refuse. The deploy that ships this change re-ingests, so the window is one deploy.
- [`array_agg(DISTINCT ...)` over 6,181 chunks on every run start] → One indexed-free scan of one table; the fingerprint query already reads every chunk. Cost is bounded by the fingerprint's own cost.
- [A collection tag with `IS NULL` rows in scope hides a leak from the fingerprint's point of view] → The v2 filter mirrors retrieval exactly, so the fingerprint stays honest about what retrieval returned. Dropping the `IS NULL` branch is task 15 of the multi-collection proposal, not this change.

## Migration Plan

0. Before the implementation PR opens, validate on the claw stack (AGENTS.md, "Deployment & Validation Policy"): deploy the branch with the `archi-dev-deploy-verify` procedure, run one smoke benchmark arm and one QA run, and confirm `retrieval_identity`, both `sha256/v2:` readings, and the guard's log line in the artifacts and the logs. Name the containers checked in the PR body.
1. Merge this change to `dev`.
2. Run the backfill over `bench_out/` (dry run, then write). Commit the stamped artifacts in `fasrc/archi-bench-out`.
3. Redeploy the dev host (`deploy/scripts/redeploy.sh`). The re-ingest populates the chunk tags. Do not do this inside a running campaign.
4. Optional: run the #411 one-off cleanup. Under v2 it does not move the fingerprint.
5. Re-pin: run one baseline arm (arm 00) on a fresh deploy, then `archive_run.sh 00 <run> <yaml> --new-corpus` moves each campaign stack's v1 pin to the v2 digest and records the old pin in `repinned_from`. A stack with no pin gets the v2 digest on its first archive.
6. Start the campaign. The first #216 arm records `embedding_model` and the guard verifies the chunk tags.

Rollback: revert the merge and redeploy. Artifacts stamped by the backfill keep their extra keys; nothing reads them in v1 code.

## Open Questions

- How many orphan parents on dev have no live twin? The query is in PR #569's discussion. Under v2 the answer does not change the design; it decides whether a v1 pin on dev is already unstable.
- Does the `OpenAIEmbeddings` path ever run on a stack that is benchmarked? If not, D1's `model` kwarg branch needs a unit test only.
