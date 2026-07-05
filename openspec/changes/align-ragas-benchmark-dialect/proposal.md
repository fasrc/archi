## Why

The benchmark harness pins `ragas==0.3.5` but still hand-builds ragas 0.1 column
names (`question/contexts/answer/ground_truth`) and feeds them to `evaluate()` — a
mismatch the code itself flags with `# TODO this is likely broken now`
(`service_benchmark.py:1171`). ragas 0.3.5 expects the modern
`SingleTurnSample`/`EvaluationDataset` schema
(`user_input/retrieved_contexts/response/reference`); handing it legacy column
names makes the context metrics score over a silently shrunken denominator or fail
outright. Separately, the FASRC question banks are authored in a bespoke
`question/answer/sources` shape no external tool understands, while the
`fasrc/ragas-json-editor` browser tool already writes the exact modern ragas
schema. Aligning the harness onto ragas 0.3.5's native dialect fixes the latent
scoring bug **and** makes editor-authored banks drop-in consumable — one schema on
both sides of the seam.

## What Changes

- Migrate the RAGAS scoring path from a raw `datasets.Dataset` carrying legacy
  columns to `ragas.EvaluationDataset` built with the modern
  `user_input/retrieved_contexts/response/reference` schema. Clears the
  `# TODO likely broken`.
- The question-bank contract becomes the modern ragas dialect. The bank's
  ground-truth answer maps to **`reference`** (not `response`) — the one semantic
  the harness and the editor must agree on; getting it wrong sends ground truth
  into the agent-answer slot and empties `reference`, which the context metrics
  require.
- Accept legacy banks via a normalize-on-read shim (`question→user_input`,
  `answer→reference`, `contexts→retrieved_contexts`) so existing and
  externally-supplied files keep loading. Migrate the in-repo banks
  (`fasrc_ragas_queries`, `anchor_questions`, `queries`) to the modern schema — and
  normalize the **separate anchor-file load path** too (`_merge_anchor_questions`
  keys dedup on `question`), or migrated anchors get silently skipped.
  (`snow_ragas_queries_pt1.json` is gitignored/operator-local — an operator handoff,
  not a tracked migration.)
- Score each metric only over rows whose required columns are populated — skip
  `context_precision`/`context_recall` when `reference` is empty (the intentional
  `should_refuse` rows) — by scoring each metric over its **own eligible
  `EvaluationDataset`** and reporting its `n_scored / n_total`, instead of a skip-NaN
  mean over the full set. This eligibility **composes on top of** PR #92's run-status
  filtering (failed/degraded rows), which supplies the scorable candidate set.
- archi extension fields (`sources`, `source_match_field`, `anchor_type`, `notes`)
  are carried alongside but never passed into ragas; SOURCES mode and anchor
  slicing keep reading them directly.
- Update `fasrc_ragas_queries.README.md`, `docs/docs/benchmarking.md`, the config
  field docs, and the ragas/argilla tests to the modern contract.

**Depends on PR #92** (`fix/benchmark-per-question-resilience`, change
`harden-benchmark-and-agent-resilience`), which rewrites the `service_benchmark.py`
run loop into a thin call site over a new pure, unit-tested
`src/utils/benchmark_resilience.py`. This change sequences **after** #92 and builds
on that module: keyed per-question attribution is #92's — this change adds the ragas
dialect, the normalizer, per-metric data-eligibility, and the per-metric scored
denominators that #92's whole-column aggregate cannot express, as further helpers on
the same seam.

Companion (separate repo, its own proposal): `fasrc/ragas-json-editor` (a) preserves
the extension fields on edit so a bank round-trips through the browser tool without
losing SOURCES/anchor data, and (b) fixes its `ragas.js` import mapping from
`answer→response` to `answer→reference` so a legacy bank imported through the editor
keeps ground truth in `reference`. The normalize-on-read contract is the shared seam;
(b) is a dependency for the drop-in editor round-trip (the harness's own dialect fix
does not depend on it).

## Capabilities

### New Capabilities
<!-- none — all behavior lives in the existing benchmarking capability -->

### Modified Capabilities
- `retrieval-benchmarking`: the question-bank / harness-schema contract changes
  from the bespoke `question/answer/sources` shape to ragas 0.3.5's modern
  `user_input/retrieved_contexts/response/reference` dialect. Adds requirements for
  RAGAS scoring construction via `EvaluationDataset`, per-metric row eligibility
  when required columns are empty, and per-metric scored denominators
  (`n_scored / n_total`). (Keyed per-question attribution is **not** added here — it
  is owned by the sibling `benchmark-run-resilience` capability / PR #92, which also
  supplies the scorable candidate set this change scores over.)

## Impact

- **Code:** `src/bin/service_benchmark.py` — dialect wiring only (bank load,
  required-field validation on `user_input`, ragas record build →
  `EvaluationDataset`); line anchors are re-taken against the **post-#92** loop,
  which moves this region substantially. The normalizer and the per-metric
  eligibility helper land in / beside `src/utils/benchmark_resilience.py` (#92's
  pure module), not a standalone new file.
- **Data:** `examples/benchmarking/{fasrc_ragas_queries,anchor_questions,queries}.json`
  migrated to modern schema; the anchor-file load path (`_merge_anchor_questions`,
  keys on `question`) normalized alongside. `snow_ragas_queries_pt1.json` is
  gitignored/operator-local — an operator handoff, not an in-repo migration.
- **Config / docs:** `queries_path` field docs in `base-config.yaml`,
  `fasrc_ragas_queries.README.md`, `docs/docs/benchmarking.md`.
- **Tests:** `test_ragas_evaluator_local_mode.py`,
  `test_benchmark_ragas_only_match_fields.py`, `tests/smoke/ragas_smoke.py`.
- **Dependencies:** none — `ragas==0.3.5` is already pinned in the benchmark
  Dockerfiles; no new packages.
- **Cross-repo:** `fasrc/ragas-json-editor` companion change (preserve-rest on
  edit); shared normalize contract is the seam.
