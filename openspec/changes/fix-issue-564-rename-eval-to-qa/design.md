## Context

`src/cli/qa_eval.py` defines `eval_cli` (`@click.group(name="eval")`, line 26) and, under it,
`qa_cli` (`@eval_cli.group(name="qa", invoke_without_command=True)`, line 31) with the
`prepare`, `run`, and `score` subcommands. `src/cli/cli_main.py:24` imports `eval_cli` and
`:1114` registers it. The repo pins `click==8.1.7` (`pyproject.toml:17`).

Callers of the old spelling: `scripts/benchmarking/feature_matrix/qa_arm.sh:87,88,158`
(`"$FM_ARCHI" eval qa …`), `qa_prepare.sh:39`, and the fake `archi` in
`test_feature_matrix_wrappers.sh` (line 106 matches `"$1 $2" = "eval qa"`; lines 314, 578,
613, 614 grep the recorded calls for `eval qa …`). The gate runs that harness.

## Goals / Non-Goals

**Goals:** `archi qa …` is the one documented spelling; `archi eval qa …` keeps working for
one release with a deprecation line on stderr; `archi --help` lists `qa` and not `eval`.

**Non-Goals:** renaming or nesting `archi evaluate`; removing the alias; any change to
options, arguments, exit codes, or workflow behaviour.

## Decisions

**D1 — `qa_cli` becomes a top-level group.** Change line 31 to
`@click.group(name="qa", invoke_without_command=True)`. Its options and its three
subcommands do not change. `cli_main.py` imports `qa_cli` and registers it with
`cli.add_command(qa_cli)` in the place where `eval_cli` was registered.

**D2 — `eval_cli` stays as a hidden alias group that holds the same `qa_cli` object.**
`eval_cli` keeps its name and module so old imports work. It becomes
`@click.group(name="eval", cls=_DeprecatedAliasGroup, hidden=True)`, and the module calls
`eval_cli.add_command(qa_cli)` after `qa_cli` is defined. No command is copied, so the two
paths can not drift. `cli_main.py` registers both `qa_cli` and `eval_cli`.

**D3 — the notice prints from `parse_args`, not from the group callback.** A Click group
callback does not run when a subcommand gets `--help` (the eager `--help` exits while the
subcommand parses, before the parent callback). `_DeprecatedAliasGroup(click.Group)`
overrides `parse_args(self, ctx, args)`: it calls
`click.echo(DEPRECATION_NOTICE, err=True)` and then returns `super().parse_args(ctx, args)`.
The notice then prints for `archi eval qa run …` and for `archi eval qa run --help`. A probe
with click 8.1.7 on the host confirmed both cases and confirmed that `hidden=True` keeps
`eval` out of `archi --help`.

**D4 — the notice text is one module constant.** `DEPRECATION_NOTICE = "Deprecated: 'archi
eval qa' is now 'archi qa'. The 'archi eval' alias will be removed in a later release."`
Tests import the constant; they do not copy the string.

**D5 — the notice goes to stderr only.** Stdout of the alias path is byte-identical to the
`archi qa` path. Tests use `CliRunner(mix_stderr=False)` and assert on `result.stderr` and
`result.stdout` separately.

**D6 — existing tests move to `qa_cli`.** The five tests in
`tests/unit/evaluation/qa/test_cli.py` invoke `eval_cli` with a leading `"qa"` argument.
Change each one to invoke `qa_cli` and drop the leading `"qa"`. Keep every test name and
every assertion. One test asserts exact output (`== "QA evaluation completed: run-1
(scored)\n"`, line 46); it must stay on the `qa_cli` path, which prints no notice.

**D7 — the doc sweep uses word boundaries.** The issue's literal criterion
`grep -rn "archi eval" docs/ scripts/` also matches every `archi evaluate`, which this change
must not touch. The criterion this change uses is in D8. `docs/site/` is build output and is
not tracked; use `git grep`, which skips it.

**D8 — the acceptance grep.** After the sweep,
`git grep -nE "archi eval([^u]|$)|eval qa" -- docs scripts` prints only the deprecation note
lines in `docs/docs/cli_reference.md`. Every other mention says `archi qa`. Rename the
heading ``### `archi eval qa` `` to ``### `archi qa` `` and change the one link to it
(`docs/docs/evaluation.md:1282`, `cli_reference.md#archi-eval-qa`) to `#archi-qa`.

**D9 — the composite form keeps its shape.** `archi eval qa --dataset …` becomes
`archi qa --dataset …`; `archi eval qa prepare|run|score` becomes `archi qa
prepare|run|score`. In the wrapper harness, the fake's in-flight drift check becomes
`[ "$1" = "qa" ]`, and each recorded-call grep loses the `eval ` prefix.

## Risks / Trade-offs

- An external script that parses `archi --help` for `eval` stops finding it. Accepted: the
  alias still runs, and the issue asks for a hidden alias.
- Proposal docs under `docs/docs/proposals/` are records of past plans. They still get the
  new spelling, because the acceptance grep covers all of `docs/` and the old spelling there
  would send a reader to the deprecated path.
- The `qa-evaluation-trial` requirement "Ported QA-evaluation CLI runs all phases" names
  `archi eval qa`. This change ADDS a requirement for the new name and does not rewrite that
  ported requirement; its text is a record of the port.
