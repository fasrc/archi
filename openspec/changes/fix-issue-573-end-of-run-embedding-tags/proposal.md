## Why

The start guard from #570 (`collection_readiness()`, `src/utils/benchmark_provenance.py:990`)
checks the searched collection's `embedding_model` chunk tags once, before the first
question. Nothing checks them again. The corpus fingerprint v2 is model-neutral on purpose
(#570 design D3), so a re-embed of the same text under another model between the start
guard and the end reading leaves `corpus_fingerprint_before == corpus_fingerprint` and
`corpus_unchanged_at_endpoints == true`. The run then records the configured model in
`retrieval_identity.embedding_model` although some questions searched other vectors.

- Harness: `ResultHandler.handle_results` (`src/bin/service_benchmark.py:503`) compares
  only the two fingerprints and the two category-map digests.
- QA: `end_readings()` (`src/evaluation/qa/provenance.py:79`) compares only the two
  fingerprints.
- `compare_runs.py` (`corpus_gate`, `embedding_gate`, `qa_corpus_reason`) and
  `feature_matrix/archive_run.sh:204` read no end-of-run tag state, because none exists.

Issue: fasrc/archi#573 (Codex adversarial review, round 2 of the #570 pre-PR loop).

## What Changes

- A shared tested helper in `src/utils/benchmark_provenance.py` reads the end tag state
  (`embedding_model_tags`, `untagged_chunk_count`) through `readiness_counts()` and compares
  it with the start record: `False` (changed) when a tag other than the run's
  `embedding_model` appears or the untagged count rises; `None` when not observed; else
  `True`. Ordinary same-model ingestion is not a change; the corpus fingerprint already
  reports content churn.
- The harness arm record and the QA manifest (and so `summary.provenance`) get two keys:
  `embedding_tags_end` (the end state, or an `<unavailable: …>` marker) and
  `embedding_tags_unchanged_at_endpoints` (`true` / `false` / `null`). A QA manifest written
  before this change never gains a `null` key when it is scored or retried later.
- Consumers treat a recorded value other than `true` like `corpus_unchanged_at_endpoints`
  other than `true`: the harness leaderboard withholds ranks and warns; the per-arm HTML and
  Markdown reports show an alert; `compare_runs.py` refuses the arm, a noise replicate, or a
  QA run (no flag admits it); `archive_run.sh` refuses the artifact in single-arm mode and in
  `--sweep` mode (`sweep_tools.archive`). An ABSENT key (an artifact that predates this
  change) is unknowable, not unstable, and passes as before.

## Non-goals

- The tag does NOT enter the corpus fingerprint digest. That would make `compare_runs` G3
  refuse every #216 embedding A/B (issue constraint).
- No per-question tag readings. The two endpoint readings bracket the arm, as the corpus
  readings do; a change and revert wholly inside an arm is not detected (same limit as the
  corpus fingerprint).
- No new field in the start record `retrieval_identity`, and no change to the start guard's
  refusal rules.
- No change to `category_slice.py` (it already requires a stable corpus), `qa_arm.sh`, or
  the backfill script.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `eval-corpus-provenance`: runs record the end-of-run tag state and its comparison with
  the start; comparison and archive tooling refuse a run whose tag state changed.

## Impact

- Code: `src/utils/benchmark_provenance.py`, `src/bin/service_benchmark.py`,
  `src/utils/generate_benchmark_report.py`, `src/evaluation/qa/provenance.py`,
  `src/evaluation/qa/workflow.py` (call sites), `scripts/benchmarking/compare_runs.py`,
  `scripts/benchmarking/feature_matrix/archive_run.sh`,
  `scripts/benchmarking/feature_matrix/sweep_tools.py`.
- Tests: `tests/unit/test_collection_readiness.py`,
  `tests/unit/test_benchmark_corpus_fingerprint.py`, the leaderboard and report-provenance
  tests, `tests/unit/evaluation/qa/test_provenance.py`,
  `tests/unit/evaluation/qa/test_workflow.py`, `tests/unit/test_compare_runs.py`,
  `tests/unit/test_sweep_tools.py`,
  `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`.
- Artifacts: additive keys only.
- Docs: `docs/docs/interpreting_benchmark_results.md` names the new key next to
  `corpus_unchanged_at_endpoints`.
