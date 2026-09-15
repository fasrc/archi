# Record the host that ran a benchmark, and render it in both reports

## Why

No benchmark artifact names the machine that produced it. On `origin/dev` at `3170498c`,
`git grep -n "gethostname\|getfqdn\|platform.node" -- '*.py'` returns nothing.
`add_metadata` (`src/bin/service_benchmark.py:448`) writes the run-level provenance block —
`time`, `git_info`, `git_info_captured_at`, `code_version`, `config_versions`,
`corpus_snapshot_id`, `corpus_fingerprint` — and names no machine. Two artifacts produced on
two machines therefore look identical in provenance, and no check refuses the comparison.

Three measured quantities move with the machine, so this is a correctness gap, not a
cosmetic one:

1. `ingest_wall_seconds` — the scrape and the embed both run on the local host.
2. Per-question `time_elapsed` — a VPN hop to the vLLM endpoint costs what a local call
   does not.
3. The embedding floats — the template defaults the embedder to `device: cpu`
   (`src/cli/templates/base-config.yaml:212`), and two processors can select different math
   kernels. The last-bit differences flip retrieval order on near-ties.

Nothing catches item 3 today. `CORPUS_STATE_QUERY` (`src/bin/service_benchmark.py:102-125`)
hashes document text, chunk text and parent text, never the vectors. `KEY_SETTING_PATHS`
(`src/utils/benchmark_provenance.py:115-140`) records `data_manager.embedding_name` only.

A **retrieval** arm run on a second host is caught today, but only by accident: its corpus
fingerprint differs. An **ingest** arm is not caught at all, and
`compare_runs.py --corpus-differs-by-design` (`scripts/benchmarking/compare_runs.py:519`)
relaxes exactly the check that caught the retrieval case. A cross-host ingest arm passes
every gate and prints a verdict.

The #396 campaign pins the host as a fixed factor by operator discipline
(`docs/docs/proposals/feature-matrix-campaign-2026.md` §2, the "Host" row). This change does
not block that campaign. It makes the discipline checkable, for that campaign's artifacts
and for the next one.

## What Changes

- `get_git_information()` (`src/cli/managers/templates_manager.py:79`) gains a `host` block
  holding `hostname` and `cpu_model`. A new module-level helper does the capture, so the
  host logic is testable without a `git` subprocess. The call site at `:750` already writes
  the returned dict to `<base_dir>/git_info.yaml`, so no new write path appears.
- Capture never raises and never fails a deploy, and the two fields fail differently. An
  unreadable processor model gives `None` for that key alone. An unreadable hostname gives
  `None` for the whole block, so a partial capture cannot invent a fourth state that every
  consumer would read as a machine named `None`. The capture records the hostname and the
  processor model only — no secret, no user path.
- `add_metadata` (`src/bin/service_benchmark.py:448`) lifts the `host` block out of the
  loaded YAML into `metadata["host"]` and leaves no second copy inside `git_info`. The lift
  happens **before** the metadata literal is built, because `"git_info": additional_info`
  stores a reference to the same dict rather than a copy.
- The same literal gains a `host_captured_at` string, mirroring `git_info_captured_at`
  (`src/bin/service_benchmark.py:464`). Issue #433 asks for the deploy-time caveat "in the
  artifact and in the docs", and a source comment does not reach a reader holding only the
  JSON — which is exactly what that neighbouring field's own comment says.
- `parse_benchmark_results` (`src/utils/generate_benchmark_report.py:78`) reads
  `metadata.get("host", _HOST_NOT_RECORDED)` with a new module-level sentinel, copying the
  `_INGEST_NOT_RECORDED` pattern at `:58` that #417 added.
- Both renderers gain a host row beside the code row — `format_version_html` (`:222`) and
  `format_version_markdown` (`:1001`). Each renders three distinct texts for the three
  distinct facts: the key is absent, the value is `null`, the value is an object.
- The early return `if not code and not config: return ""` in both renderers widens to
  admit a recorded host. Without that widening, an artifact carrying a host but no version
  digests would render no host row at all.
- `compare_runs.py` gains a `host` field on `Arm` (`:183`), a host row in `provenance_rows`
  (`:441`), and a note printed under the provenance table when the arms name different
  hosts. The note never changes the exit code and never refuses a comparison.
- `docs/docs/interpreting_benchmark_results.md` names `host` in the results-file tree
  (`:659-663`) and in the per-run provenance table (`:713-722`), and states why deploy-time
  capture is correct for a host although `:701-712` warns that it is wrong for a commit.

## Capabilities

### New Capabilities

- `benchmark-run-provenance`: what a benchmark artifact records about the run that produced
  it, and what the reports and the comparison tool guarantee about that record. No
  capability directory under `openspec/specs/` covers this today —
  `deploy-config-provisioning` records the provenance of the *config checkout*, not of a
  benchmark run, and `retrieval-benchmarking` states the A/B protocol without any
  provenance requirement. This change therefore adds its requirements rather than
  modifying existing ones.

### Modified Capabilities

None.

## Impact

- `src/cli/managers/templates_manager.py` — the capture helper and its call from
  `get_git_information`.
- `src/bin/service_benchmark.py` — the lift in `add_metadata`.
- `src/utils/generate_benchmark_report.py` — the sentinel, the parse, and both renderers.
- `scripts/benchmarking/compare_runs.py` — the `Arm` field, the provenance row, and the
  mismatch note.
- `docs/docs/interpreting_benchmark_results.md` — the tree, the table, and one sentence in
  §5.E.
- New tests: `tests/unit/test_benchmark_host_provenance.py`. Extensions to
  `tests/unit/test_benchmark_report_markdown.py`,
  `tests/unit/test_benchmark_report_html_provenance.py`, and
  `tests/unit/test_compare_runs.py`.
- All four production files are black-clean under the gate's pinned black 24.10.0, measured
  on 2026-09-05, so an in-place edit reformats nothing around it and `diff-cover` scores
  only the new lines.
- The gate measures `--cov=src` (`scripts/gate.sh:147`), so the `compare_runs.py` lines report no
  coverage data. Their unit tests are the evidence, not a percentage.
- **Not** in scope: a refusal rule on a host mismatch, the embedder-device gap in
  `KEY_SETTING_PATHS`, and any backfill of the artifacts already in `bench_out/`. Their host
  is unrecoverable, and #426 owns artifact migration.
