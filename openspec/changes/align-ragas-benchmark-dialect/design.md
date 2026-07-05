## Context

`service_benchmark.py` loads a question bank (`queries_path` → staged as
`QandA.txt`), answers each question with the live agent, then scores the run with
RAGAS. The scoring path builds a raw `datasets.Dataset` with ragas **0.1** column
names (`question/contexts/answer/ground_truth`, `:1173`) and hands it to
`ragas.evaluate()` — but the benchmark image pins **`ragas==0.3.5`**, whose
`evaluate()` expects the modern `EvaluationDataset`/`SingleTurnSample` schema
(`user_input/retrieved_contexts/response/reference`). The code flags this itself:
`# TODO this is likely broken now` (`:1171`). Banks are authored in a bespoke
`question/answer/sources` shape, while `fasrc/ragas-json-editor` already writes the
exact modern ragas schema — so the two are the same data in incompatible dialects.

Concurrently, **PR #92** (`harden-benchmark-and-agent-resilience`) rewrites this
same run loop into a thin call site over a new pure, unit-tested
`src/utils/benchmark_resilience.py` — isolating per-question failures, keying
results per-question, and reporting scorable counts / `n/a` aggregates. This change
**sequences after #92** and extends that module rather than re-touching the raw loop.

## Goals / Non-Goals

**Goals:**
- Feed ragas 0.3.5 the schema it actually expects; clear the `# TODO likely broken`.
- Make editor-authored banks (modern schema) drop-in consumable by the harness.
- Keep existing legacy banks working via normalize-on-read.
- Stop silently averaging RAGAS metrics over a hidden partial denominator.

**Non-Goals:**
- A run-output curation flow (loading completed `response`/`retrieved_contexts`
  records back into the editor). That is a separate use case, deferred.
- Changing SOURCES-mode URL matching (its reconciliation is a known separate gap).
- Upgrading the ragas version (0.3.5 is already pinned).

## Decisions

**1. Modern ragas 0.3.5 dialect is the canonical schema.**
`user_input/retrieved_contexts/response/reference`, built via
`EvaluationDataset.from_list`. Rationale: it is what the pinned library expects and
what the editor already emits. *Alternative rejected:* keep legacy columns and pass
`evaluate(column_map=…)` — a band-aid that leaves the harness and editor speaking
different dialects.

**2. `answer → reference`, never `answer → response`.**
In the authoring bank the ground-truth answer is `answer`; in modern ragas the
ground truth is `reference` and `response` is the *agent's* answer. The editor's
current `ragas.js` maps legacy `answer → response`, which is correct for run-output
records but wrong for authoring banks. The shared normalizer MUST encode
`answer → reference` for banks. This is the single highest-risk mapping: get it
wrong and `reference` is empty, which the context metrics require.

**3. Normalize-on-read shim, plus migrate the four in-repo banks.**
A small normalizer (`question→user_input`, `answer→reference`,
`contexts→retrieved_contexts`) runs at load so legacy and externally-supplied files
keep working; the four in-repo banks are migrated to modern schema so new authoring
is clean and the shim only ever catches foreign files. *Alternative rejected:*
migrate-only (breaks the instant anyone supplies a legacy file); shim-only (repo
banks stay legacy forever).

**4. Per-metric eligibility + per-metric denominators, layered on #92's scorable set.**
ragas runs with `raise_exceptions=False`, so an empty-`reference` row does not crash
— it becomes a NaN that pandas `.mean()` silently drops. #92 provides the run-status
scorable set (failed/degraded excluded) and per-question keyed attribution, but its
`build_ragas_aggregates` is a single whole-column `.mean()` — one denominator shared
by all four metrics — so it **cannot** express a smaller denominator for the context
metrics. This change therefore scores each metric over its **own eligible
`EvaluationDataset`** (empty-`reference` rows dropped for the context metrics),
taking #92's scorable set as the candidate pool, and reports each metric's
`n_scored / n_total`. Because each metric means over its eligible subset, the
aggregate is a mean over real rows, never a skip-NaN mean — this **structurally
avoids** the silent-denominator bug rather than routing through it. *Not owned here:*
the positional→keyed attribution and status isolation — those are #92's.
*Alternative rejected:* full-dataset-per-metric relying on NaN skip — the
silent-denominator bug itself.

**5. Extension fields ride alongside, never into ragas.**
`sources/source_match_field/anchor_type/notes` are consumed by SOURCES mode
(`:1181`) and anchor slicing (`:1483`) straight off the harness's own structures;
they are never keys in the `SingleTurnSample`.

**6. Location unification (C) is free.**
Once both sides speak the modern dialect, `queries_path` can point directly at an
editor-managed `.json`; the existing `copyfile → queries.txt → QandA.txt` staging
carries it in unchanged. No new mount, no new bridge.

## Risks / Trade-offs

- **[Editing `service_benchmark.py` trips the diff-coverage gate / black-churn trap]**
  → Largely mitigated by #92: it already extracts the loop logic into the pure,
  fully-covered `benchmark_resilience.py` and thins the call site. New logic lands as
  helpers in that module; residual edits to the big file stay minimal. Re-check with
  black-seam-scout after rebasing onto post-#92 dev.
- **[#92 shifts under this change before it merges / its structure changes in review]**
  → This change is sequenced strictly after #92 merges (task 0); line anchors and the
  exact `benchmark_resilience.py` helper names are re-taken against merged `dev`, not
  the in-review branch.
- **[ragas is a benchmark-only lazy import, absent from the unit-test env]** → Unit
  tests assert the *record shape* and normalizer behavior without importing ragas;
  the local-mode evaluator test and `ragas_smoke.py` cover the live path.
- **[Migrating the four banks could preserve stale facts]** (`anchor_questions.json`
  still has the old `--gres=gpu:N` form) → Migration is schema-only; factual
  corrections are tracked separately and out of scope here.
- **[Editor round-trip can still land ground truth in `response`]** → The editor's
  *authoring* UI writes `reference` correctly, but its `ragas.js` *import* path maps
  legacy `answer→response` (not `→reference`). A legacy bank imported through the
  editor therefore carries ground truth in `response` with an empty `reference`,
  which the harness would mis-score. The archi normalizer deliberately does NOT try
  to repair this: a populated `response` is ambiguous (it can be a legitimate
  pre-recorded agent answer), so a heuristic risks clobbering real data. Instead,
  fixing the editor's import mapping to `answer→reference` is an explicit part of the
  **companion `ragas-json-editor` change** and is a dependency for the drop-in editor
  round-trip promise — NOT for the harness's own dialect fix, which still tolerates
  hand-authored modern and legacy-`answer` banks in any order.

## Migration Plan

0. **Sequence after PR #92 merges.** Branch from post-#92 `origin/dev`; re-anchor
   line references and confirm the `benchmark_resilience.py` helper names against the
   merged loop.
1. TDD a shared normalizer (`question→user_input`, `answer→reference`,
   `contexts→retrieved_contexts`; modern records pass through unchanged), as helpers
   in / beside `benchmark_resilience.py`.
2. Rewire the harness dialect: load → normalize; build each metric's own eligible
   `EvaluationDataset` from #92's scorable set (empty-`reference` rows dropped for the
   context metrics) and report each metric's `n_scored / n_total`. (No positional-join
   replacement — #92 owns keyed per-question attribution.)
3. Migrate the four in-repo banks to modern schema (schema-only).
4. Update docs (`fasrc_ragas_queries.README.md`, `docs/docs/benchmarking.md`) and
   `base-config.yaml` field docs.
5. Update `test_ragas_evaluator_local_mode.py`,
   `test_benchmark_ragas_only_match_fields.py`, `ragas_smoke.py`; extend
   `test_benchmark_resilience.py` for the eligibility helper.
6. Gate (`bash scripts/gate.sh`, ≥80% diff coverage). A live benchmark run needs a
   rebuild of the benchmark image (non-editable install), not the chat deploy.

## Open Questions

- **Attribution key:** resolved — owned by #92 (results are keyed per-question in the
  reworked loop); this change does not choose or introduce a key.
- **Aggregation convergence:** this change scores each metric over its own eligible
  `EvaluationDataset` and produces the per-metric aggregate directly, rather than
  feeding #92's whole-column `build_ragas_aggregates` (which can't take per-metric
  subsets). Open: whether to later extend `build_ragas_aggregates` to accept
  per-metric eligible inputs and converge the two aggregation paths, or leave this
  change's per-metric path separate. Lean toward separate until a second caller needs
  the merge — coordinating a signature change into #92 mid-review is higher-risk.
- **Should the `queries_path` default example point at an editor-managed path** in
  docs, or stay repo-relative? Cosmetic; settle during docs update.
