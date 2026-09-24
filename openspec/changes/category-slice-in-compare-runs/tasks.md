## 1. Paired tests (`scripts/benchmarking/paired_tests.py`)

- [x] 1.1 `model: sonnet` — RED: `mcnemar_exact` tests — n = 0, symmetry, parity with the archi-bench-out reference over b, c in 0..20
- [x] 1.2 `model: sonnet` — GREEN: port `mcnemar_exact`
- [x] 1.3 `model: sonnet` — RED/GREEN: `holm_adjust` (monotone, capped at 1, order restored)
- [x] 1.4 `model: sonnet` — RED/GREEN: `paired_binary` for relative source hits (clean-in-both, declared sources, equal canonical source lists; differing questions listed) and for completion (common set, status ok); b = baseline success/arm failure, c = arm success/baseline failure, direction reported

## 2. Snapshot and counting (`scripts/benchmarking/category_slice.py`)

- [ ] 2.1 `model: sonnet` — RED/GREEN: load a snapshot; the four checks in order, each failure named; legacy arm → "no snapshot"; a pair with different corpus fingerprints → "no slice" even under `--corpus-differs-by-design`
- [ ] 2.2 `model: opus` — RED/GREEN: build the per-category table from `category_attribution` (from `preflight-category-census`) over the snapshot map; the two-source-different-categories test from #525 and the uncategorized-first-source test from #538, asserted end to end through the slice; overall figures unchanged
- [ ] 2.3 `model: sonnet` — RED/GREEN: per-metric power shown per row of the table, with no verdict for an underpowered metric
- [ ] 2.4 `model: sonnet` — RED/GREEN: trace scan over benchmark `messages` and QA `tool_calls`
- [ ] 2.5 `model: opus` — RED/GREEN: map-mismatch rule over all four readings — routed arm void, failed start or end reading void (including a failed start with a matching end), unrouted pair slice-only, trace hit void, legacy no-op
- [ ] 2.6 `model: sonnet` — RED/GREEN: QA join rule — equal readings join; end differs, readings missing, a reading unavailable → refusal named; legacy arm joins
- [ ] 2.7 `model: opus` — RED/GREEN: QA agent-spec identity — first confirm whether `agent_spec.resolved.md` (`src/evaluation/qa/workflow.py:347-367`) is the prompt file's exact bytes; if it is, compare `agent_spec_sha256` to the arm's `agent_md_sha256`, else compare against the digest of the resolver's output for that file; a mismatch refuses the QA run naming both digests

## 3. Wire into `compare_runs.py`

- [ ] 3.1 `model: opus` — run the black-seam-scout agent on `compare_runs.py` before editing
- [ ] 3.2 `model: opus` — RED/GREEN: one selector resolver (label first, then recorded `services.benchmarking.name`; unknown or ambiguous → exit 1 listing candidates) used by `--baseline`, `--primary`, `--routes-on-category` and `--qa-run`; `--primary` test-name validation; report header prints label and name per arm
- [ ] 3.3 `model: opus` — RED/GREEN: `load_qa_run` reads `category_map_readings.json` and `answers.jsonl` `tool_calls`; `parse_qa_run_specs` applies the join rule
- [ ] 3.4 `model: opus` — RED/GREEN: `build_report` adds `paired_tests`, `category_slice`, `map_rule` blocks; voided pairs emit no numbers; overall figures unchanged
- [ ] 3.5 `model: sonnet` — RED/GREEN: `render_markdown` sections for the three blocks, and the `--json` output carries them

## 4. Docs

- [ ] 4.1 `model: haiku` — `scripts/benchmarking/README.md`: `--primary`, `--routes-on-category`, the selector syntax (label or recorded name), the b/c direction, the new report sections, and what voids a comparison

## 5. Verify

- [ ] 5.1 `model: sonnet` — `bash scripts/gate.sh` green; `openspec validate category-slice-in-compare-runs --strict`
