## Context

#570 added a start guard and a model-neutral corpus fingerprint v2. The model is carried by
`retrieval_identity.embedding_model` and is checked against the chunks' `embedding_model`
tags only by the start guard. A re-embed under another model during a run is invisible to
both fingerprints. This change adds an end reading of the tag state and makes every
consumer that already refuses `corpus_unchanged_at_endpoints != true` refuse a changed tag
state too.

Code as of `origin/dev` `0189e2dd`:

- `readiness_counts(cursor, collection)` → `(chunk_count, usable, untagged, tags)`,
  `src/utils/benchmark_provenance.py:979`. `tags` is the list of distinct non-null tags.
- `collection_readiness(pool, identity)` → dict with `chunk_count`, `usable_chunk_count`,
  `untagged_chunk_count`, `embedding_model_source` (`:990-1043`). It refuses any tag other
  than `identity.embedding_model`, so at the start every tag equals the run's model.
- `retrieval_record(identity, readiness)` merges identity and readiness (`:1046`), so the
  start record holds `embedding_model` and `untagged_chunk_count`.
- Current ingestion tags every new chunk with the configured model
  (`src/data_manager/vectorstore/postgres_vectorstore.py:206`).
- Harness: `ResultHandler.check_collection` (`src/bin/service_benchmark.py:442`),
  `handle_results` (`:503`), `arms_incomparability_reason` (`:217`, corpus check at `:264`),
  leaderboard warnings (`:1278-1289`), run loop (`:2370-2400`).
- Per-arm report: `src/utils/generate_benchmark_report.py:159` copies
  `corpus_unchanged_at_endpoints` into the provenance block; the HTML renderer (`:218`) and
  the Markdown renderer (`:1193`) print a line from it.
- QA: `start_readings`, `end_readings`, `PROVENANCE_KEYS`, `summary_fields`
  (`src/evaluation/qa/provenance.py`); callers `src/evaluation/qa/workflow.py:368` (reset),
  `:383`, `:597`, `:876`, `:995-1001` (retry copy), `:1141`, `:1228`.
- `compare_runs.py`: `Arm` parse (`:353`), `embedding_gate` (`:730`),
  `check_noise_replicates` (`:1111`, corpus stability at `:1167`), `load_qa_run` corpus dict
  (`:1975-1984`), `qa_corpus_reason` (`:1987`).
- `feature_matrix/archive_run.sh:204` refuses `corpus_unchanged_at_endpoints is not True`
  in single-arm mode. `--sweep` mode (`:44-73`) hands every check to
  `sweep_tools.archive` (`scripts/benchmarking/feature_matrix/sweep_tools.py:317`, per-arm
  fingerprint check at `:377-385`).

## Decisions

### D1. A change is a foreign tag or more untagged chunks

The end reading takes `(embedding_model_tags, untagged_chunk_count)` through
`readiness_counts()`, under the same retrieval filter as the start guard. The tag state
**changed** when either holds:

1. the end tag list holds a tag other than the start record's `embedding_model`; or
2. the end `untagged_chunk_count` is greater than the start record's.

Otherwise it is **unchanged**. Rationale — the check reports only what a model change can
cause and the model-neutral fingerprint cannot see:

- A re-embed under model B with tagging code adds tag B → rule 1 → changed.
- A re-embed or write by code that sets no tag raises the untagged count → rule 2 → changed.
- Ordinary ingestion with the same model (today's code tags every chunk) adds only the
  run's own tag: `[] → ["m1"]` on a legacy collection is unchanged. The corpus fingerprint
  already reports that content change, and `--corpus-differs-by-design` governs it there.
- Deleting or replacing legacy untagged chunks lowers the untagged count → unchanged (the
  fingerprint reports the content change).

Because a "changed" state always means vectors of another model, or vectors with no
recorded model, appeared during the arm, the consumers refuse it with no flag (D7).

The start record needs no new field: the start guard guarantees every start tag equals
`embedding_model`, and `untagged_chunk_count` is already recorded. `retrieval_identity` keeps
its seven fields; no existing exact-key assertion changes.

### D2. One shared helper pair, in `benchmark_provenance.py`

- `live_embedding_tag_state(pool, config) -> Dict[str, Any]`: reads `readiness_counts()` for
  `_searched_collection(config)` on `pool`, returns
  `{"embedding_model_tags": sorted(tags), "untagged_chunk_count": untagged}`. It raises on
  failure, like `live_corpus_fingerprint`.
- `embedding_tags_unchanged(start: Any, end: Any) -> Optional[bool]`: pure. Returns `None`
  when `start` is not a dict, or its `embedding_model` is not a non-empty `str`, or its
  `untagged_chunk_count` is not an `int` (a `bool` does not count); or when `end` is not a
  dict with a list `embedding_model_tags` and an int (not bool) `untagged_chunk_count` (an
  `<unavailable: …>` marker string is not a dict). Otherwise returns `False` when D1 rule 1
  or rule 2 holds, else `True`.

Both callers (harness, QA) use these two functions, so a RAGAS arm and a QA run over one
stack agree, as the fingerprint does.

### D3. Harness: read at the end, next to the end fingerprint

`ResultHandler.get_embedding_tag_state(config)` wraps `live_embedding_tag_state` with the
factory pool and never raises: on failure it logs a warning and returns
`f"{ResultHandler.CORPUS_UNAVAILABLE} {exc}>"`, the same marker shape as
`get_corpus_fingerprint`. `handle_results` calls it right after `get_corpus_fingerprint`,
computes `embedding_tags_unchanged(retrieval_identity, end)`, logs a warning when the value
is `False`, and writes two keys into `current_results`, next to the corpus keys:

- `"embedding_tags_end"`: the end state dict, or the marker string;
- `"embedding_tags_unchanged_at_endpoints"`: `True` / `False` / `None`.

`handle_results` already receives the start record as its `retrieval_identity` keyword, so
the run loop does not change. A caller that passes `retrieval_identity=None` records `None`.

### D4. Harness consumers treat the key like corpus stability

- `arms_incomparability_reason`: a record whose `embedding_tags_unchanged_at_endpoints` is
  present and not `True` returns
  `"the embedding model tags changed while an arm was running"` (same "present and not
  True" rule as the corpus key at `:264`; an absent key is skipped).
- Leaderboard warnings (`:1278-1289`): `False` → "the embedding model tags changed while
  variant '<name>' was running; some questions searched vectors of another model or of no
  recorded model"; present and `None` → "embedding tag stability is unknown for variant
  '<name>'".
- Per-arm report (`generate_benchmark_report.py`): the provenance block copies the key with
  a sentinel that keeps "absent" distinct from `None`. Both renderers add one line: `False`
  → an alert with the warning text above; `None` → "unknown"; absent → nothing. `True` →
  "The embedding model tags did not change during the run".

### D5. QA: `end_readings` takes the start record; absent stays absent

`end_readings(config, spec, before, identity_before=None)` adds the two keys of D3. Without
the search tool both are `None`. Otherwise it reads the end state through
`live_embedding_tag_state(direct_pool(config), config)` inside a `try` that turns a failure
into the `<unavailable: …>` marker (like `_reading`), and compares with
`embedding_tags_unchanged(identity_before, end)`.

The two keys go into a NEW tuple `TAG_KEYS`, not into `PROVENANCE_KEYS`. Every place that
copies or clears provenance handles `TAG_KEYS` **only when the key is present** in the
source, so a manifest written before this change never gains a `null` tag key:

- `summary_fields(manifest)`: `PROVENANCE_KEYS` as today, plus each `TAG_KEYS` key that is
  in `manifest`.
- Retry copy (`workflow.py:995-1001`, no fresh attempts): copy each `TAG_KEYS` key that is in
  `parent_manifest`. Put this in a small helper in `provenance.py`
  (`carried_readings(parent_manifest)`) and call it from `workflow.py`, so the logic is
  unit-tested in one place.
- Reset (`workflow.py:368`): pop `TAG_KEYS` too.

The two `end_readings` call sites pass the start record:
`manifest.get("retrieval_identity")` at `:597`, and
`corpus_readings.get("retrieval_identity")` at `:1141`.

Two existing test sites must change with this, and only these: the exact
`summary["provenance"]` key set at `tests/unit/evaluation/qa/test_workflow.py:295` (it gains
the two keys, because that run's manifest has them), and the 3-argument `end_readings` stub
at `test_workflow.py:372-379` (it gains `identity_before=None`).

### D6. Where a run is refused

The rule everywhere: key **absent** → as before (the run predates this change); key present
and `True` → as before; key present and `False` or `None` → refused (a failed reading is
not a certified state, the same as the corpus readings).

### D7. `compare_runs.py` refuses, with no flag

- `Arm` gets `embedding_tags_unchanged: Optional[bool]` and `embedding_tags_recorded: bool`
  (whether the key is present). A refusal applies when the key is recorded and its value is
  not `True`.
- `embedding_gate` refuses (raises `CompareError` with `EXIT_GATE`) when any arm in `arms`
  matches, before its other checks. The reason names the arm(s) and says the embedding model
  tags changed during the arm, so some questions searched vectors of another model or of no
  recorded model. No flag admits it. `--corpus-differs-by-design` is NOT a way out: D1 makes
  "changed" mean a model change, not ordinary content churn.
- `check_noise_replicates` applies the same refusal to every replicate and the baseline in
  its `scope`, next to the corpus-stability check at `:1167`, whatever
  `--corpus-differs-by-design` says. A void replicate must not widen sigma.
- `load_qa_run`'s `corpus` dict copies `embedding_tags_unchanged_at_endpoints` only when the
  key is present in `provenance`.
- `qa_corpus_reason`, after the existing "no reading → join" early return: a key present and
  not `True` returns `"the embedding model tags changed while it answered"` (`False`) or
  `"its embedding tag reading is unavailable"` (`None`).

### D8. Archive tooling refuses, in both modes

- Single-arm `archive_run.sh`: right after the corpus refusal at `:204-205`, when the
  artifact has the key and its value is not `True`, print
  `REFUSED: the embedding model tags changed during the run (…); the arm is void` and exit 2.
  An absent key passes.
- `--sweep` mode: in `sweep_tools.archive`'s per-arm loop, next to the fingerprint check at
  `:377-385`, raise `SweepError(f"{name}: embedding model tags changed during the arm …")`
  under the same rule. The sweep path's existing failure handling reports it (its exit code
  is the sweep path's own, not necessarily 2). Tests: `tests/unit/test_sweep_tools.py`.
- Self-tests: `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` gains
  two single-arm cases (present `false` → refused; present `true` → archived). That file runs
  from `scripts/gate.sh`, not from `tests/`.

## Risks / Trade-offs

- An end reading that fails records `None`, and every consumer refuses a present `None`.
  That matches the corpus readings: an unobserved state is not a certified one.
- A re-embed and revert wholly inside one arm is not detected. Same limit as the corpus
  fingerprint; per-question readings are out of scope.
- A re-embed under the SAME model with tagging code is not "changed". It does not change
  which model served the questions, which is what `retrieval_identity.embedding_model`
  claims.
- `category_slice.py` and the backfill are not changed: the slice already requires the
  corpus to be stable, and the backfill cannot invent an end reading for an old artifact.
