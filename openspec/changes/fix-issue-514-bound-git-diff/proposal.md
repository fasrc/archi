# Bound the git diff captured into benchmark artifact metadata

## Why

`get_git_information()` (`src/cli/managers/templates_manager.py:138-165` at `4b253e26`) runs
a bare `git diff` and stores the whole output under `git_diff`:

```python
diff_comm = ["git", "diff"]
meta_data["git_diff"] = subprocess.check_output(
    diff_comm, encoding="UTF-8", cwd=wd
)
```

`archi create` writes that mapping to `<base_dir>/git_info.yaml` (`:810-816`). The benchmark
image copies the file (`src/cli/templates/dockerfiles/Dockerfile-benchmarks:27`), and
`ResultHandler.add_metadata()` (`src/bin/service_benchmark.py:449-465`) places it verbatim
under `metadata.git_info` in every artifact. Nothing on that path bounds the field: no
`--stat`, no size cap, no path filter.

**The defect, measured** by the issue author on 2026-09-19 (#514). The golden-set artifact
`banks/golden/results/benchmarking-ragas-devbench-20260919_204930.json` in
`fasrc/archi-bench-out` (commit `29714a9`) is 35.2 MB. `metadata.git_info.git_diff` is
32.9 MB of that, 93%. The run data is 1.49 MB. The diff holds the former contents of 48
deleted `bench_out/` artifacts: the FASRC dev checkout is pinned at `v2026.08.0`, which
predates PR #505 (stop tracking `bench_out` from the archi repo), so those deletions sit in
the working tree and ride into every artifact written from that checkout. The 2026-08-21/22
`ragas-205` pair (about 11 MB each) came from the same mechanism. Every byte is permanent
once pushed. The reproduce block in the issue needs the FASRC checkout and was not re-run
here; the capture site above was re-read at `4b253e26` and is unchanged.

**Three readers depend on the field's emptiness.** `src/utils/benchmark_provenance.py`
derives `deploy_git_dirty` as `bool((info.get("git_diff") or "").strip())` at `:499`
(`code_version`), `:524` (`collect_code_version`) and `:609` (`reconstruct_version_stamp`).
`compare_runs.py` Procedure E and both report renderers
(`src/utils/generate_benchmark_report.py:303`, `:1079`) read the flag. Whatever replaces the
raw diff must stay empty for a clean tree and non-empty for a dirty one, or the flag inverts.

**A trap in the issue's own plan, measured on 2026-09-20** (host git 2.55.0). The capture
runs with `cwd = Path(__file__).parent`, which is `src/cli/managers/`. The issue suggests
`git diff -- . ':(exclude)bench_out'`. In a throwaway repository with three uncommitted
changes (`src/cli/managers/x.py` edited, `bench_out/art1.json` deleted,
`nested/bench_out/n.json` edited), run from `src/cli/managers/`:

| command | files in the diff |
|---|---|
| `git diff` (today) | all three |
| `git diff -- . ':(exclude)bench_out'` (the issue's text) | `src/cli/managers/x.py` **only** |
| `git diff -- ':(top,exclude)bench_out'` | `src/cli/managers/x.py`, `nested/bench_out/n.json` |

A bare pathspec is relative to the working directory, so the literal suggestion restricts
the diff to the capture's own directory and a change anywhere else in the tree stops
marking the deploy dirty. The `top` magic anchors the exclusion at the repository root and
keeps whole-tree coverage. The same probe confirmed the other properties the design leans
on: a clean tree gives 0 bytes for both `git diff` and `git diff --stat`; a `bench_out`-only
change gives 0 bytes for both with the top-anchored filter and 129 056 bytes without it;
and `git diff --stat=100,,50` over 300 changed files gives 52 lines and 4 993 bytes with the
`300 files changed` summary line intact.

## What Changes

- A new sibling module `src/cli/managers/git_diff_capture.py` owns the capture. It runs two
  commands in the capture directory, both with the pathspec `-- ':(top,exclude)bench_out'`:
  a unified diff, and a `--stat=120,,200` summary. It bounds the unified diff to
  `GIT_DIFF_MAX_BYTES = 256_000` UTF-8 bytes, cut on a line boundary and never inside a
  character, and never cut to empty when the input was not empty.
- `get_git_information()` gains an optional `wd` parameter (default unchanged:
  `Path(__file__).parent`) and writes four keys in place of the raw `git_diff`: `git_diff`
  (bounded, same key name), `git_diff_stat`, `git_diff_truncated`, `git_diff_original_bytes`.
- Top-level `bench_out/` never enters the diff, the stat, or the dirty flag.
- New unit tests in `tests/unit/test_benchmark_git_diff_bound.py` run the capture against
  real throwaway git repositories: clean tree, oversized change, small change,
  `bench_out`-only change, and capture from a subdirectory.
- Two lines in the artifact tree of `docs/docs/interpreting_benchmark_results.md` name the
  new fields, and one sentence in Procedure E says the diff is bounded.

## What Does Not Change

- The three `benchmark_provenance.py` readers, `compare_runs.py` Procedure E, and the report
  renderers. `deploy_git_dirty` keeps its meaning and its derivation. Their existing tests
  stay green untouched.
- Existing artifacts in `fasrc/archi-bench-out`, including the 35 MB blob. No history rewrite;
  shrinking them is a separate, explicitly approved decision.
- The failure mode when `git` itself fails. `check_output` raises today and still raises.
- Deployments already created. `git_info.yaml` is written once at `archi create` and then
  frozen, so the fix takes effect at the next `archi create` from a release that carries it.
  The FASRC dev checkout at `v2026.08.0` keeps the old capture until it is redeployed.

## Impact

- Files: `src/cli/managers/git_diff_capture.py` (new), `src/cli/managers/templates_manager.py`
  (about 10 lines in `get_git_information`), `tests/unit/test_benchmark_git_diff_bound.py`
  (new), `docs/docs/interpreting_benchmark_results.md` (3 lines).
- Capability: `benchmark-run-provenance`. The capability lives only in the change
  directories of #433 and #442 (both merged, neither archived) and is not yet under
  `openspec/specs/`, so this delta uses `ADDED` requirements.
- Risk: low. The new module is a leaf with one caller. All four production files touched are
  black-clean under black 24.10.0 at `4b253e26`, so an in-place edit reformats nothing.
