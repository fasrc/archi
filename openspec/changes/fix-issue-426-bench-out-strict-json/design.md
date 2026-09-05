# Design — strict-JSON migration of the committed benchmark artifacts

## The decision that shapes everything: recompute, never hand-edit

A `NaN` token could be replaced with `null` by a regular expression over the file text. That is
the wrong tool twice over. It cannot recompute a `<metric>_scored` denominator, and it cannot
prove it left the rest of the file alone.

The migration instead loads each artifact with plain `json.loads` (CPython accepts a bare `NaN` by
default), recomputes the denominators, and re-dumps through the same call the harness itself makes:

```python
json.dump(json_safe(obj), f, indent=4, allow_nan=False)
```

`allow_nan=False` is the load-bearing half. If anything non-finite survives `json_safe`, the dump
raises instead of writing another invalid file. The migration cannot half-succeed.

## Why the result is provably minimal

Round-tripping all 18 artifacts through that exact call and comparing to the bytes on disk:

- **8 artifacts are byte-identical.** Key order, `indent=4`, and the absence of a trailing newline
  all survive, so those 8 are not written and never enter the diff.
- **10 artifacts differ**, and they are exactly the 10 that `grep -l 'NaN'` finds.

That is the measurement that turns "this should not churn anything" into a fact. It also pins the
trailing-newline rule: the artifacts end at the closing brace with no newline, because
`ResultHandler.dump` (`src/bin/service_benchmark.py:546`) closes the handle straight after
`json.dump`. Write `f.write("\n")` after the dump and all 18 files churn, hiding the 10 real
changes in 18 files of noise.

## Recomputing `<metric>_scored` from the artifact alone

`score_metrics_per_eligibility` (`src/utils/benchmark_schema.py:550`) produces
`f"{len(finite)} of {total}"` where `total` is the number of rows handed to it and `finite` is the
number of finite scores that reached the aggregate. Both halves are recoverable from the artifact:

- `total` — the rows handed in are the scorable ones (`scorable_items`,
  `src/utils/benchmark_resilience.py:59`), so `total` is the count of `single_question_results`
  entries whose `status` is absent or `"ok"`. An unmarked legacy row counts as scorable, which is
  what `is_scorable` (`src/utils/benchmark_resilience.py:54`) already decides.
- `finite` — a per-question cell is written only for a row the metric was eligible on, and
  `_as_score_cell` folds every non-finite spelling onto `NaN` there. So the count of finite numeric
  cells for that metric, over the scorable rows, is exactly `len(finite)`. An ineligible row has no
  cell at all and is correctly not counted. A genuine `0.0` **is** counted — "unscored" and "scored
  zero" stay distinct, which is the distinction `json_safe` exists to protect.

Booleans are excluded explicitly. `bool` is a subclass of `int` in Python, and a careless numeric
branch would count a `matched: true` cell as a score.

`source_scored_count` does not end in `_scored` in the `<metric>_scored` sense — it is the
source-accuracy denominator from `_source_scorable_count`
(`src/bin/service_benchmark.py:1715`), computed from the question bank rather than from finite
judge scores. It is excluded by name.

## Why a test, when the issue asked only for a data edit

Issue #426 describes a throwaway script in `/tmp` and a before/after table in the PR body. That
delivers the bytes but leaves nothing behind: the next artifact committed from an older harness, or
a hand-edited denominator, reintroduces the defect with no signal. The project also requires a
failing test before any fix, and a data migration is no exception — the red here is the current
tree failing the invariant on 10 files.

`tests/unit/test_bench_out_artifacts.py` therefore asserts, over every committed artifact:

1. it parses under a `parse_constant` that raises, and
2. every arm's `<metric>_scored` equals the recomputed count, and
3. no committed report under `bench_out/` contains the word `nan`.

This is a `tests/` file, not a `src/` file, so it does not make this a behaviour change and it does
not appear in the coverage measurement.

Two details the test has to get right:

- **Word boundaries on the `nan` search.** The reports contain the word "maintenance". A substring
  search reports 21 hits on a file whose real count is 0. Use `\bnan\b`. Verified: after the
  re-render, `\bnan\b` matches 0 times across all 20 reports, and it already matches 0 times in the
  8 reports belonging to clean artifacts, so a repository-wide assertion passes.
- **Parse the 28 MB once.** `bench_out/` holds 28 MB of JSON. Parse it in a module-scoped fixture
  and share it, rather than re-reading per test. Resolve the directory from `__file__`, not from the
  process working directory, and skip cleanly if it is absent.

## Re-rendering the reports

`backfill_report_provenance.py` stamps provenance before it re-renders. A `--dry-run` over all 18
artifacts reports "0 of 18 artifact(s) would change" — every artifact is already stamped — so the
re-render pass contributes no provenance churn on top of the migration.

The two render paths differ deliberately, and the difference decides the diff:

- `regenerate_html` rewrites a `_report.html` only if one already exists. 9 of the 10 migrated
  artifacts have one; `benchmarking-ragas-bench-20260704_183010` does not and gains none.
- `regenerate_md` **creates** a missing `_report.md`. No `_report.md` exists anywhere in
  `bench_out/` today, so all 10 are new files, about 1.6 MB in total.

Pass the 10 migrated paths explicitly rather than letting the script glob. The default glob covers
all 18 and would create 8 more markdown reports for artifacts whose data did not change — files
nobody reviewed, in a PR about ten artifacts.

Verified on a copy in `/tmp`: the renderers handle a `null` cell without raising and without
printing "None" for it. The only two `None` strings in the re-rendered HTML are the corpus
fingerprint pair (`<code>None</code> → <code>None</code>`), which are present in the committed file
before the migration too.

## Alternatives rejected

- **Delete the 10 artifacts.** They are the pre-campaign evidence record; the plan calls them
  non-comparable, not worthless.
- **Fix the readers instead.** Every reader in the repository already tolerates both spellings. The
  files are wrong, not the readers, and the external tools that reject them (`jq`, a browser) are
  not ours to change.
- **Rewrite all 18 for uniformity.** The 8 clean files are already correct, byte for byte, under the
  same call. Rewriting them would add noise and prove nothing.
- **Skip the markdown reports** to avoid 1.6 MB. This was weighed. Acceptance criterion 3 of the
  issue asks for the md siblings, and the script documents creating a missing one as its intended
  recovery path, so they are created. The size cost is stated in the proposal's Impact section so a
  reviewer can reverse the choice knowingly.
