## Why

Two top-level CLI verbs share one domain: `archi eval` (the QA evaluator,
`src/cli/qa_eval.py:26`) sits beside `archi evaluate` (the RAGAS benchmark,
`src/cli/cli_main.py:1112`). An operator who types one gets the other's help. The QA
evaluator's real work already lives in a sub-group named `qa` (`src/cli/qa_eval.py:31`),
so the `eval` level carries no meaning of its own. Child 4 of #320; the operator decided
the rename on 2026-09-27 (issue #564 body, "Decision").

## What Changes

- The `qa` group (`qa_cli`) becomes a top-level command: `archi qa` (composite),
  `archi qa prepare`, `archi qa run`, `archi qa score`. Options, arguments, and
  behaviour are unchanged.
- `archi eval` stays as a **hidden, deprecated alias** for one release. `archi eval qa …`
  runs the same `qa_cli` group and first prints one deprecation line to stderr. It does
  not appear in `archi --help`.
- The feature-matrix wrappers (`qa_arm.sh`, `qa_prepare.sh`) and their test harness
  (`test_feature_matrix_wrappers.sh`) call `archi qa …`.
- Docs (`docs/docs/**`), `scripts/benchmarking/**` prose and help strings, and
  `tests/unit/test_compare_runs.py` docstrings say `archi qa`. `cli_reference.md` gets
  one deprecation note for the alias.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `qa-evaluation-trial`: ADDS a requirement that names the command `archi qa` and keeps
  `archi eval qa` as a deprecated hidden alias.

## Impact

- Code: `src/cli/qa_eval.py`, `src/cli/cli_main.py` (two `add_command` lines and the import).
- Tests: `tests/unit/evaluation/qa/test_cli.py`.
- Scripts: `scripts/benchmarking/feature_matrix/{qa_arm.sh,qa_prepare.sh,lib.sh,test_feature_matrix_wrappers.sh}`,
  `scripts/benchmarking/{README.md,compare_runs.py,ragas_bank_to_qa_dataset.py}` (prose only
  in the last two Python files).
- Docs: `docs/docs/{benchmarking,cli_reference,evaluation,interpreting_benchmark_results,user_guide}.md`
  and `docs/docs/proposals/*.md` mentions. The anchor `cli_reference.md#archi-eval-qa`
  becomes `#archi-qa`.
- No change to `archi evaluate`. No config, deploy, or control-plane file changes.
