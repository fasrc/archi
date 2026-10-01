## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-564-rename-eval-to-qa` exists, cut from `origin/dev` at
      `26e6429e`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-564-rename-eval-to-qa --strict` passed on the host. The
      `openspec` CLI is **not usable in this container** — do not run it, and do not add a task
      that does. It is already green.
- [x] 0.3 The red is measured at `26e6429e` (probe in `## Commands`): `qa run --help` exits
      **2** ("No such command"); `eval qa run --help` exits **0** with **empty** stderr;
      `archi --help` lists `eval`. When done: `0`, `0` with the notice on stderr, and `eval`
      not listed. Module baseline: `tests/unit/evaluation/qa/test_cli.py` **5 passed**.
- [x] 0.4 Every design decision is made in `design.md` (D1–D9). Follow it. Where the issue
      body and the design differ, the design wins (in particular D7/D8 replace the issue's
      literal `grep "archi eval"`, which also matches `archi evaluate`).

## Rules that apply to EVERY task below

- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed and the loop halts there.
- Do not touch `archi evaluate`, its options, or its docs. Every edit below is about the
  QA evaluator only.
- Before you add a test, `grep -n 'def test_' <file>` and pick a name that is not already
  used. The gate runs no linter, so a duplicate `def test_...` silently replaces the older
  test while the suite stays green.
- Add new tests **between** existing tests or after the last one with a blank-line gap, then
  `git diff` the file and read the trailing context line. Inserting inside an existing test
  body silently steals that test's last assertion.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/ | grep -c '^-.*def test_'` must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer.
- The PR body goes in `/tmp`, never in the repo. Do not add an entry to `docs/questions.md`.
- Do not edit the tracked `tasks.md` symlink, `scripts/gate.sh`, `Makefile`, `ralph.conf`,
  `PROMPT.md`, or anything under `deploy/`, `config/`, `.github/`, or `hooks/`.

## 1. Rename the group, keep a hidden deprecated alias

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.
      **Red** — in `tests/unit/evaluation/qa/test_cli.py`, add these tests. Use
      `CliRunner(mix_stderr=False)` for each (click is pinned at 8.1.7, where that argument
      exists), and import `DEPRECATION_NOTICE` and `qa_cli` from `src.cli.qa_eval`:
      - `test_qa_group_is_named_qa`: `qa_cli.name == "qa"`.
      - `test_qa_subcommands_answer_help`: invoke `qa_cli` with `["run", "--help"]` and with
        `["score", "--help"]`; each exits 0 and `result.stderr == ""`.
      - `test_eval_alias_runs_qa_and_prints_the_notice_once`: monkeypatch
        `qa_cli_module.QAWorkflow` with the `_Workflow` fake (as the existing composite test
        does), then invoke `eval_cli` with `["qa", <the composite options>]` and invoke
        `qa_cli` with the same options minus `"qa"`. Assert both exit 0, both `stdout` are
        equal, `alias.stderr.count(DEPRECATION_NOTICE) == 1`, and `direct.stderr == ""`.
      - `test_eval_alias_prints_the_notice_on_subcommand_help`: invoke `eval_cli` with
        `["qa", "run", "--help"]`; exit 0 and `DEPRECATION_NOTICE in result.stderr`. (This
        is the case a group callback would miss — design D3.)
      - `test_top_level_help_lists_qa_and_hides_eval`: `import src.cli.cli_main as cli_main`;
        `monkeypatch.setattr(sys, "argv", ["archi", "--help"])`; call `cli_main.main()` inside
        `pytest.raises(SystemExit)` and assert the exit code is 0; read `capsys`. The
        commands section must contain a line starting with `  qa ` and a line starting with
        `  evaluate `, and no line starting with `  eval `. Calling `main()` is what covers
        the two `add_command` lines in `cli_main.py`, which run only inside `main()`.
      Run the module: the module fails to import (no `DEPRECATION_NOTICE`), so every test
      errors — that is the red. `test_qa_group_is_named_qa` alone would pass at `26e6429e`
      (the sub-group is already named `qa`); it guards the name, it is not the red.
      **Green** — follow design D1–D5 exactly:
      - `src/cli/qa_eval.py`: add `DEPRECATION_NOTICE` (text in D4) and
        `class _DeprecatedAliasGroup(click.Group)` whose `parse_args` echoes the notice to
        stderr and then returns `super().parse_args(ctx, args)`. Make `eval_cli`
        `@click.group(name="eval", cls=_DeprecatedAliasGroup, hidden=True)` with docstring
        `"""Deprecated alias for 'archi qa'."""`. Make `qa_cli`
        `@click.group(name="qa", invoke_without_command=True)` (no longer under `eval_cli`)
        with docstring `"""Run the Archi QA evaluator."""` if it has none. After the
        `score` subcommand, call `eval_cli.add_command(qa_cli)`.
      - `src/cli/cli_main.py`: `from src.cli.qa_eval import eval_cli, qa_cli`; in `main()`
        register `cli.add_command(qa_cli)` where `eval_cli` was, and keep
        `cli.add_command(eval_cli)` on the next line.
      **Migrate** the five existing tests (design D6): each `CliRunner().invoke(eval_cli,
      ["qa", …])` becomes `CliRunner().invoke(qa_cli, […])` with the leading `"qa"` removed.
      Keep every test name and every assertion, including the exact-output assertion on
      line 46. If `eval_cli` is then unused in the file except by the new alias tests, keep
      the import for those.
      Run the module: **10 passed**. Run the probe in `## Commands`: `0`, `0` + notice,
      `eval listed: False`. Gate, then commit `rename archi eval to archi qa with a hidden
      deprecated alias (#564)`.

## 2. The feature-matrix wrappers call `archi qa`

- [ ] 2.1 Update the wrappers and their harness together (one commit; the harness is the
      red for the wrappers and runs inside the gate).
      - `scripts/benchmarking/feature_matrix/qa_arm.sh`: lines 87, 88, 158 —
        `"$FM_ARCHI" eval qa …` → `"$FM_ARCHI" qa …`. Comments on lines 23 and 46 →
        `archi qa`, `archi qa run`.
      - `scripts/benchmarking/feature_matrix/qa_prepare.sh`: line 39 → `"$FM_ARCHI" qa prepare …`;
        the line-7 comment → `archi qa`.
      - `scripts/benchmarking/feature_matrix/lib.sh`: comments on lines 278 and 380 →
        `archi qa`.
      - `scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh`: first change
        ONLY the expectations — line 106 → `[ "\$1" = "qa" ]` (keep the backslash escape,
        it sits in an unquoted heredoc), line 101 comment → `` \`qa\` ``, line 19 comment,
        and the call greps on lines 314, 578, 613, 614 drop the leading `eval ` (line 314's
        `ok` message on 317 too). Run
        `bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh` and see
        it report `not ok` lines (red). Then edit the two wrappers as above and re-run: every
        line `ok`, exit 0.
      Gate, then commit `call archi qa from the feature-matrix wrappers (#564)`.

## 3. Docs and script prose say `archi qa`

- [ ] 3.1 Sweep the remaining mentions. The red is the acceptance grep:
      `git grep -nE "archi eval([^u]|$)|eval qa" -- docs scripts tests` prints many lines
      now. Change each to the new spelling (design D8, D9):
      `archi eval qa prepare|run|score` → `archi qa prepare|run|score`;
      `archi eval qa` (composite, or the command in general) → `archi qa`; ``` ``archi eval
      qa`` ``` in Python docstrings and help strings → ``` ``archi qa`` ```.
      Files: `docs/docs/{benchmarking,cli_reference,evaluation,interpreting_benchmark_results,user_guide}.md`,
      `docs/docs/proposals/{categories-action-plan,feature-matrix-campaign-2026,icl-and-query-category-mapping}.md`,
      `scripts/benchmarking/{README.md,compare_runs.py,ragas_bank_to_qa_dataset.py}`,
      `tests/unit/test_compare_runs.py` (docstring at line 2064 only).
      Rename the heading in `cli_reference.md` to ``### `archi qa` `` and change the link in
      `evaluation.md` (`cli_reference.md#archi-eval-qa`) to `cli_reference.md#archi-qa`.
      Directly under that heading, add one note:
      `> **Deprecated alias:** \`archi eval qa …\` still runs the same command for one
      release and prints a deprecation notice to stderr. Use \`archi qa\`.`
      Do not change any `archi evaluate` text.
      Green: the acceptance grep prints only the deprecation-note line in
      `docs/docs/cli_reference.md`. `git grep -n "archi-eval-qa" -- docs` prints nothing.
      `python -m pytest tests/unit/test_compare_runs.py -q` passes with the same count as
      before. Gate, then commit `say archi qa in docs and benchmarking prose (#564)`.

## 4. Publish

- [ ] 4.1 Run `bash scripts/gate.sh` on the final tip; it must exit 0. Push the branch. It
      was cut with `checkout -b`, so push with
      `git push -u origin fix/issue-564-rename-eval-to-qa`. Confirm the push landed on
      **fasrc/archi**: `git ls-remote --heads origin fix/issue-564-rename-eval-to-qa` must
      print the same SHA as `git rev-parse HEAD`. If it prints nothing, stop and write the
      halt reason to `STATUS.md`.
      Write the PR body to `/tmp/pr-body-564.md` — **never** under `docs/` — with the
      literal line `Closes #564`, the measured red (the three probe results at `26e6429e`),
      the test count before and after (5 → 10 in `test_cli.py`), and the deprecation-notice
      text. If `gh pr list --repo fasrc/archi --head fix/issue-564-rename-eval-to-qa` is
      empty, run
      `gh pr create --repo fasrc/archi --base dev --title "rename archi eval to archi qa, keep a hidden deprecated alias (#564)" --body-file /tmp/pr-body-564.md`.
      If the review gate already opened one,
      `gh pr edit <pr> --repo fasrc/archi --body-file /tmp/pr-body-564.md` instead.
      Verify the link: `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences`
      must list 564. Then STOP. Do not merge this PR. A human merges, in daylight.

## Commands

```bash
# the module's own suite — 5 passed at 26e6429e, 10 when done
python -m pytest tests/unit/evaluation/qa/test_cli.py -q --no-header

# the red probe — at 26e6429e: "qa run --help 2", "eval qa run --help 0 ''", "eval listed: True"
python3 - <<'EOF'
import sys; sys.path.insert(0, ".")
from click.testing import CliRunner
import src.cli.cli_main as m
for name in ("qa_cli", "eval_cli"):
    if hasattr(m, name): m.cli.add_command(getattr(m, name))
r = CliRunner(mix_stderr=False)
for a in (["qa", "run", "--help"], ["eval", "qa", "run", "--help"]):
    x = r.invoke(m.cli, a); print(" ".join(a), x.exit_code, repr(x.stderr[:80]))
x = r.invoke(m.cli, ["--help"]); print("eval listed:", "  eval " in x.stdout)
EOF

# the wrapper harness (also run by the gate)
bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh

# the acceptance grep — only the deprecation note may remain
git grep -nE "archi eval([^u]|$)|eval qa" -- docs scripts tests

# no test was deleted — must print 0
git diff origin/dev -- tests/ | grep -c '^-.*def test_'

# the gate, before every commit
bash scripts/gate.sh
```
