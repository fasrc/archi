## Context

`scripts/benchmarking/compare_runs.py` (2438 lines at `origin/dev` 5d72ed49) loads each arm
(`build_arm`, `:270-326`), gates it (G4, `corpus_gate` `:511-566`, divergence, G8 `g8_gate`
`:2107-2191`), joins QA runs (`parse_qa_run_specs` `:2305-2333`, `load_qa_run` `:1585-1618`,
`qa_block` `:1621-1714`), slices by `SLICE_FIELDS` (`:85`, `slice_block` `:1406-1536`) and
renders Markdown (`render_markdown` `:1796+`). It reads no `messages`, has no URL rule and no
paired binary test. Its tests are `tests/unit/test_compare_runs.py`, with `_row`, `_artifact`
and `_qa_run` fixtures (`:47-79`, `:112-183`, `:1272-1328`).

Inputs from `record-category-map-digest`: per arm `category_map_sha256_start`,
`category_map_sha256_end`, `category_map_unchanged_at_endpoints`, `category_map_file`; the
shared `canonical_source_url` and record escaping in `src/utils/benchmark_provenance.py`.
Input from `sweep-mode-feature-matrix-wrappers`: `category_map_readings.json` in each QA run
directory, `{"start": <digest>, "end": <digest>}`.

## Goals / Non-Goals

**Goals:** the two paired tests with primary/secondary handling; the category slice behind
the four snapshot checks; the counting rules; the cross-arm void; the QA join rule, including
the plan W8 check that each QA run used its arm's prompt (against the arm's `agent_md_sha256`,
which `record-category-map-digest` records).

**Non-Goals:** reading Postgres (the slice reads only the snapshot file); the bank-coverage
census (W7); changing any existing gate or exit code.

## Decisions

1. **Two new modules, thin call sites.** `paired_tests.py` holds `mcnemar_exact`,
   `holm_adjust`, and `paired_binary(baseline, arm, outcome) -> {b, c, n, p}`.
   `category_slice.py` holds snapshot loading and checks and the map-mismatch rule; it calls
   `category_attribution.py` (introduced by `preflight-category-census`) for ownership,
   coverage and power, so the census and the slice share one implementation. `compare_runs.py` gains flag parsing, calls, and render sections only —
   it is large and already carries a black/diff-cover risk.
2. **Relative hit, from `matched`.** The per-row hit is `any(entry.get("matched") ...)` over
   `reference_sources_metadata`, the rule #498 and `mcnemar_exact`'s caller used (operator,
   2026-09-24). Strict accuracy (`recomputed_source_accuracy`, `:1058`) is shown as descriptive.
3. **Completion is `status == "ok"`** (`Arm.has_clean_row`, `:200-203`), paired over the G4
   common set (operator, 2026-09-24). The no-`tool_call` count (the archi-bench-out
   `is_blowout` definition) is descriptive and named "no tool call", never "blowout".
4. **Holm over secondaries only.** Primaries are pre-registered and read raw; the family for
   adjustment is the set of secondaries in the report (the plan's two for rung 0).
5. **Snapshot path is relative to the artifact.** `category_map_file` is a basename; the slice
   opens it in the artifact's directory. Parsing reverses the producer's percent-escaping and
   verifies the file hash before parsing anything.
6. **Row identity is the question text**, as `build_arm` already indexes. Ownership uses the
   bank row's declared `reference_sources_metadata` order.
7. **Trace scan is a pure function** over `messages` (`type == "tool_call"`, `tool_name`) and
   QA `tool_calls` (`name`), so the void rule is testable without artifacts.
8. **QA readings are read by `load_qa_run`** from `category_map_readings.json`; the join
   check lives in `category_slice.py` and runs in `parse_qa_run_specs` after the label check.
   Refusal is a named `CompareError` at the existing gate exit code, so a scripted W8 run
   stops instead of silently joining.

## Risks / Trade-offs

- [Ported test drifts from the reference] → a parity test over a fixed (b, c) grid against a
  vendored copy of the reference function body.
- [Legacy artifacts lose QA joins] → the join rule applies only to arms that record
  `category_map_sha256_end`; a test pins today's behavior for legacy arms.
- [A void arm still printing numbers] → the renderer skips every numeric section for a voided
  pair and prints the reason; a test asserts no delta row is emitted for it.
- [Tiny bank categories] → per-metric power marks them; no verdict is printed for an
  underpowered metric.
