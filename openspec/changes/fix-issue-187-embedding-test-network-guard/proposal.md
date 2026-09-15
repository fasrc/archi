## Why

Two tests in the PR-gating unit suite download ~90 MB of model weights from the HuggingFace CDN
at test time, and their only guard is `except ImportError` — which anticipates the *library* being
absent, not the *weights* being unreachable. On 2026-08-02 the CDN faltered and both tests raised
`ConnectionError` from `cas-server.xethub.hf.co`, reddening PR #184 — an authentication fix that
touched no ingestion code. A re-run of the identical commit passed; PR #185, minutes earlier off
the same base, passed the same job in 4m17s.

Two costs, both real. A **false red on an unrelated PR**, which trains reviewers to re-run rather
than read a failure. And **CI wall-clock**: the retry storm took `unit-tests` from ~4 minutes to
**18m11s**.

The deeper problem is that these are not unit tests. `test_embedding_performance_realistic`
(`tests/unit/test_ingestion_pipeline_isolation.py:217`) is explicitly a performance benchmark, and
the sibling's own docstring says "On CPU, embedding is very slow (30-50+ seconds per file)"
(`:123-124`). A suite that gates every pull request should not reach the public internet or spend
30-50s per file embedding.

## What Changes

Both halves of issue #187's menu, because neither alone is sufficient — see design D1.

- **(b) Move the two tests out of the gating suite.** `test_embedding_model_works` (`:116`) and
  `test_embedding_performance_realistic` (`:217`) move from
  `tests/unit/test_ingestion_pipeline_isolation.py` to a new
  `tests/smoke/test_embedding_benchmarks.py`. `scripts/gate.sh:146` runs
  `python -m pytest tests/unit/` with an explicit path, and `pyproject.toml:74` sets
  `testpaths = ["tests/unit"]`, so relocation removes them from every gating path **without
  editing the gate, CI, or any protected file**. `tests/smoke/` is the repo's existing home for
  deliberate, infrastructure-dependent tests (documented at `docs/docs/developer_guide.md:230`).
- **(a) Broaden the guard where they now live.** The relocated tests catch a named tuple of
  transport/offline exception types alongside `ImportError` and skip with a reason that names the
  network. This is what makes the deliberate run honest: an operator running the benchmarks during
  a CDN outage gets a loud skip, not a red failure.
- **A shared helper** in the new module loads the model or skips, so the guard exists once rather
  than being duplicated in both tests as it is today (`:128-154`, `:226-278`).
- **Discoverability.** `docs/docs/developer_guide.md` documents how to run the relocated
  benchmarks on purpose, so moving them out of the gate does not make them invisible.

Not breaking: no `src/` behaviour changes, no public contract changes. The gating suite loses two
tests that exercised only third-party embedding code, never `src/` (verified in task 1.3).

## Capabilities

### New Capabilities

- `gating-suite-hermeticity`: the test suite that gates every pull request is hermetic — it does
  not depend on an external network, and a test that legitimately needs one runs deliberately,
  outside the gate, and reports a network failure as a loud skip rather than a red failure or a
  silent pass.

### Modified Capabilities

<!-- None. `ingest-processing` owns the behaviour of the ingestion pipeline; this change moves
     two tests that exercise third-party embedding construction and asserts nothing new about
     ingestion itself. No requirement of any existing capability changes. -->

## Impact

- **Tests**: `tests/unit/test_ingestion_pipeline_isolation.py` loses two methods (`:116-154`,
  `:217-278`) and the now-unused imports they alone required. New file
  `tests/smoke/test_embedding_benchmarks.py` holds both, plus the shared skip helper and a new
  test that proves the guard skips on a simulated network failure.
- **Config**: `pyproject.toml` — none required. `testpaths` already points at `tests/unit` and the
  gate passes an explicit path, so no marker or `addopts` change is needed. Recorded as decision
  D2 so a reviewer knows the omission is deliberate rather than forgotten.
- **Docs**: `docs/docs/developer_guide.md` — a short subsection on running the embedding
  benchmarks deliberately, near the existing smoke-test instructions (`:230`).
- **CI**: no workflow edits. This is what keeps the change inside the unattended nightly's
  authority — option (c) from the issue (pre-caching weights in CI) would edit
  `.github/workflows/**` and is explicitly out of scope (design D3).
- **Coverage**: the gate measures `--cov=src` only (`scripts/gate.sh:146`), so test-file changes
  carry no diff-coverage obligation. The relocated tests never covered `src/` lines — task 1.3
  verifies this before the move, so the gating suite's `src/` coverage is unchanged.
- **Unchanged, deliberately**: the two tests' assertions and timing printouts. This change moves
  and guards them; it does not rewrite what they measure, and it does not delete them.
- **Dependencies / APIs / deployment**: none.
