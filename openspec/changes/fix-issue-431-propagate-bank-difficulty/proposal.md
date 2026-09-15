# Propagate a bank row's difficulty into the per-question result

## Why

`compare_runs.py` slices a paired comparison by two bank fields,
`SLICE_FIELDS = ("anchor_type", "difficulty")`
(`scripts/benchmarking/compare_runs.py:85`), but it slices on a field only when both arms
carry it. The harness never writes `difficulty`. `_answer_and_score_question` copies one
bank field onto the per-question result — `anchor_type`, at
`src/bin/service_benchmark.py:1911-1915` — and nothing else, so every artifact the current
harness writes carries no `difficulty` and the slice is skipped rather than shown.

That gap is load-bearing for how the project asks people to read a benchmark.
`docs/docs/interpreting_benchmark_results.md:690` (Procedure D) says "Report the bank
sliced by `difficulty`, not as one number. A single mean over 40 easy, 27 medium and 6
hard questions hides everything interesting." The same page then carries an admonition at
`docs/docs/interpreting_benchmark_results.md:578-586` conceding the slice is unavailable
in practice. The instruction and the tool are both ready; the four missing lines are in
the harness.

The bank data already exists. `ragas-jeopardy-master.json` carries `difficulty` on all 73
rows (easy 40 / medium 27 / hard 6). `normalize_record`
(`src/utils/benchmark_schema.py:109-124`) copies a record with `dict(record)` and renames
only the legacy dialect keys, so an extension field such as `difficulty` already reaches
`question_item` verbatim. Nothing between the bank and the result drops it, and
`current_results["single_question_results"]` (`src/bin/service_benchmark.py:408`) stores
`q_results` with no key allowlist. The field stops at the one copy site.

## What Changes

- `_answer_and_score_question` copies `difficulty` from the bank row onto `q_results`,
  beside the existing `anchor_type` copy.
- The copy is **conditional**, unlike `anchor_type`'s. `anchor_type` is written
  unconditionally and falls back to `""`
  (`src/bin/service_benchmark.py:1911-1915`); `difficulty` is written only when the bank
  row carries it, so a bank without the field produces an artifact byte-identical in shape
  to today's. See `design.md` for why the two fields differ here.
- A seam test pins the written key against `compare_runs.SLICE_FIELDS`, so the producer
  and the consumer cannot drift apart under a rename.
- `docs/docs/interpreting_benchmark_results.md` — the admonition at :578-586 states the
  gap as closed and names the change that closed it; Procedure D's field tree at :685 says
  the same.
- `docs/docs/benchmarking.md` — the "Preparing the Queries File" field table gains
  `difficulty` as an optional field that propagates to results when present.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `retrieval-benchmarking`: gains one requirement covering the propagation of a bank row's
  `difficulty` into `single_question_results`. The capability directory exists at
  `openspec/specs/retrieval-benchmarking/spec.md` and is archived, so the delta could
  legally modify a requirement — but no existing requirement covers what the harness
  writes onto a result. "Grounded FASRC question banks in the harness schema"
  (`openspec/specs/retrieval-benchmarking/spec.md:33`) is about what a **bank** carries,
  not about what survives into an artifact. The delta therefore uses `## ADDED
  Requirements` against an existing capability.

## Impact

- `src/bin/service_benchmark.py` — four lines in `_answer_and_score_question`. The file is
  black-clean at `3170498c`, so the edit will not reflow it and drag unrelated lines into
  the `diff-cover` denominator.
- `tests/unit/test_benchmark_resilience.py` — the file that owns the
  `_answer_and_score_question` seam (`_StubBenchmarker`, :91). Also black-clean.
- `docs/docs/interpreting_benchmark_results.md`, `docs/docs/benchmarking.md` — prose only.
- **Not** edited: `src/utils/benchmark_argilla.py`. Pushing `difficulty` to Argilla as a
  `TermsMetadataProperty` is a separate gap the same page already records as "Gap 6"
  (`docs/docs/interpreting_benchmark_results.md:893-897`). It needs an Argilla schema
  migration, and it is not what #431 asks for.
- **Not** edited: any question bank. Adding `difficulty` to the FASRC bank
  (`fasrc_ragas_queries.json`) is an `archi-config` change in another repository, and that
  repository is not reachable from this run. #431 lists it as optional.
- Unblocks Procedure D and #419's `difficulty` slice for any run against
  `ragas-jeopardy-master.json`.
