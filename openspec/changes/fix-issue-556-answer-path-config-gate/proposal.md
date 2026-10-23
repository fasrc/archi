# Refuse to compare arms whose answer-path configuration differs

## Why

`scripts/benchmarking/compare_runs.py` is the one tested place the benchmark gates live
(G3–G9, Procedure E). It refuses arms that saw different documents (G3), that asked
different questions (G4), or that did not use the configuration that was selected
(Procedure E). It does **not** compare one arm's recorded configuration with another's.
`config_version.divergence_from_selected_file` compares a run with **its own** selected
file, never with the other arm.

That gap let a like-for-like comparison ship with a hidden answer-path difference. The
2026-09-19 golden-set run had `services.chat_app.context_editing: null`, and its fm-00
baseline carried the 32768-token in-loop bound with `keep: 1`. 6 of 109 questions were lost
to context overflow. With the bound restored (archi-config#23) the loss went from 6 to 0
(bench-out `c6a3cb4`). The in-loop context bound and the recursion limit decide which
questions the agent can finish at all, so a delta between such arms measures the
configuration, not the change under test.

Measured at `0ddc96e1` (this branch's base) with two single-arm artifacts that differ only
in `services.chat_app.context_editing` (one set, one absent): `main` returns `0`. Two arms of
which one records no `configuration` at all: `main` returns `0`. Both must be `2`.

This is issue #556, split from #513 (step 3) with the operator on 2026-09-26. The decision
recorded on the issue is a **fixed list of refused paths with a per-path override**, and
that body is the authoritative work order.

## What Changes

One new gate in `compare_runs.py`, one new flag, and the documentation that names them.
`scripts/` and `tests/unit/` only; no `src/` change.

- **G10 — one answer path.** A gate that reads
  `services.chat_app.context_editing` and `services.chat_app.recursion_limit` from every
  arm's recorded `configuration` and refuses with `EXIT_GATE` (2) when any arm's value
  differs from another's. An absent key is its own value, distinct from a recorded `null`,
  so "absent in one arm, present in the other" — the case that happened — refuses. The
  refusal names every differing path and each arm's recorded value.
- **`--config-differs-by-design <dotted.path>`**, repeatable, beside
  `--corpus-differs-by-design`. It waives exactly one named refused path. The gate row then
  reads `OVERRIDDEN (--config-differs-by-design <path>)` and still prints both values. It
  never hides the difference. A path that is not one of the two refused paths is a usage
  error (`EXIT_USAGE`), because it can waive nothing.
- **`services.benchmarking.agent_md_file` is reported, never refused.** Prompt arms vary it
  on purpose. When it differs the gate detail prints each arm's value.
- **An arm with no recorded `configuration`** (key missing, or not a mapping) is reported
  as `not recorded` and refuses, as G3 does for an unrecorded fingerprint, unless every
  refused path is overridden.
- The module docstring gains a G10 bullet, and `docs/docs/interpreting_benchmark_results.md`
  gains the flag row, the refusal bullet, and the Gap 1 clause.

Explicitly **not** done:

- No change to G3, G4, Procedure E, or `check_noise_replicates`. Replicates already refuse
  when they recorded distinct `config_version.digest` values, which any answer-path
  difference changes; the baseline is outside that check by design (design D7).
- No wider refused list. Refusing on any other path is a separate operator decision.
- No `src/**` edit. `src/cli/templates/base-config.yaml` is read for evidence only.
- No `fasrc/archi-config` change (#513 step 2 stays on #513).

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `retrieval-benchmarking`: the paired comparison tool refuses arms whose recorded
  answer-path settings differ, with a per-path override that prints both values. The delta
  is expressed as `ADDED` requirements because the comparison gates have no requirement in
  `openspec/specs/retrieval-benchmarking/spec.md` (that spec covers the A/B protocol, the
  banks, the measurement protocol and the recommendation); the gates exist only in
  unarchived changes (#431, #441) that used this same capability (design D8).

## Impact

- **Code**: `scripts/benchmarking/compare_runs.py` — one gate function, one dotted-path
  reader, two constants, one flag, one line in the gate list, one docstring bullet.
- **Tests**: `tests/unit/test_compare_runs.py` — the `_artifact` fixture gains a
  `configuration` argument; new tests pin the six acceptance criteria plus the null-vs-absent
  and unknown-override cases. Baseline at `0ddc96e1` is **99 passed in 0.67s**.
- **Docs**: `docs/docs/interpreting_benchmark_results.md` — Procedure C's implements line,
  its flag table, its "What it refuses" list, and the Gap 1 paragraph. No new heading and no
  new anchor, so `mkdocs build --strict` link checks are unaffected.
- **Coverage**: `scripts/gate.sh` measures `--cov=src`, so `scripts/` lines are not scored
  and `diff-cover` reports no coverable lines for this diff. The tests are the only bar the
  tool has to clear, which is why they pin behaviour rather than decorate it.
- **Runtime / deployment**: none. The tool reads finished artifacts only.
- **Release plan**: #556 is in milestone `v2026.09.0 — Benchmark integrity` (due
  2026-09-30). Without it the milestone's comparison tool passes an unlike comparison as
  like-for-like.
