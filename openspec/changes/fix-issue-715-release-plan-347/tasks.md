Rules for every task:
- This is a **docs-only** change. Change only `docs/docs/proposals/release-plan-2026.md`
  (called `$PLAN` below) and this folder's `tasks.md` ticks. No code, test, or config
  edit. Do not change any GitHub milestone or the #347 issue.
- Read `design.md` here (D1-D3) before task 1.1. It gives the 2 edit sites and the exact
  new text. Do not write the text again: copy the script in the Appendix below.
- Set `PLAN=docs/docs/proposals/release-plan-2026.md` in each shell.
- Run `bash scripts/gate.sh` before each commit, and commit only when it is green. The
  commit hook runs the gate too. Never use `--no-verify`. Add no `Co-Authored-By` or
  session trailers. Commit messages are short and lowercase.
- A gate failure in `tests/unit/test_judge_slurm.py` is the known flake #797 (this diff
  touches no code). Run the commit again. Do not edit that test.
- Never write a scratch file or a PR body under the repository (no file under `docs/`).
  Use `/tmp`.

## 1. Name the #347 exception in the plan

- [x] 1.1 Confirm the old state first: `grep -c "One exception (2026-10-09)" "$PLAN"`
      prints `0`, and `grep -c "Method records it as the one exception" "$PLAN"` prints
      `0`. Copy the Appendix script to `/tmp/apply-715.py` and run
      `python3 /tmp/apply-715.py "$PLAN"`. It must print `applied 2 edits`. If it exits
      non-zero, the old text changed on `origin/dev`: do not edit by hand; write the
      error to `STATUS.md` and stop. Then confirm: the two greps above each print `1`;
      `git diff --stat origin/dev -- "$PLAN"` shows `1 file changed, 6 insertions(+),
      2 deletions(-)`; `git diff --name-only origin/dev -- . ':!openspec/changes/fix-issue-715-release-plan-347'`
      prints only `docs/docs/proposals/release-plan-2026.md`; and
      `cd docs && mkdocs build --strict -d /tmp/mkdocs-715 2>&1 | grep -E "INFO|WARNING|ERROR"`
      prints no line that names `release-plan-2026.md` (the known lines name
      `configuration.md`, `interpreting_benchmark_results.md`, `services.md`,
      `user_guide.md`, and the "not included in the nav" note). Return to the
      repository root. Tick this box. Run `bash scripts/gate.sh` and commit when green
      (message: `docs: name the #347 exception in the release plan (#715)`).

## 2. Verify, push, and open the PR

- [ ] 2.1 Run the acceptance checks of issue #715 again and keep their output for the PR
      body: `grep -n "347" "$PLAN"`, the `git diff --name-only` command of task 1.1, the
      `mkdocs` command of task 1.1, and
      `openspec validate fix-issue-715-release-plan-347 --strict` (valid). If a check
      fails, fix it in this task first. Then tick this box, commit the tick through the
      hook (message: `mark #715 tasks complete`), and push with
      `git push -u origin fix/issue-715-release-plan-347`. Write the PR body to
      `/tmp/pr-715-body.md`. The body must hold, in this order: the line `Closes #715`;
      one short paragraph that names the operator decision of 2026-10-20 ("keep the
      exception") and the 2 sentences added (the UX-override bullet in Method, and the
      2026-08-25 scheduling paragraph), with the commit SHA of task 1.1; a note that the
      #347 row, the GitHub milestone, and the old "#347 into `v2026.09.0`" sentence do
      not change, and why (the dated record of 2026-08-25); a note that
      `openspec/changes/fix-issue-715-release-plan-347/` is the nightly driver folder and
      the only change outside `$PLAN`; and the acceptance check output. If
      `gh pr list --repo fasrc/archi --head fix/issue-715-release-plan-347 --state open`
      shows a PR, run `gh pr edit <number> --repo fasrc/archi --body-file /tmp/pr-715-body.md`.
      Otherwise run `gh pr create --repo fasrc/archi --base dev --head fix/issue-715-release-plan-347 --title "docs(#715): name the #347 exception in the release plan" --body-file /tmp/pr-715-body.md`.
      Confirm the link with
      `gh pr view <number> --repo fasrc/archi --json closingIssuesReferences -q '.closingIssuesReferences[].number'`
      (prints `715`). Then post one reply on the Codex thread of PR #658 with
      `gh api --method POST repos/fasrc/archi/pulls/658/comments/4233874792/replies -f body="..."`.
      The reply names the fix commit SHA of task 1.1 and the new PR, in one or two
      sentences. If a push, a PR write, or the reply fails with HTTP 403 or "Resource
      not accessible", leave the branch pushed, open no PR in any other repository (no
      fork), write the exact error and the reply not posted to `STATUS.md`, and stop.

## Appendix: the edit script (no task; copy it to `/tmp/apply-715.py`)

Loop 1 ran this script on a scratch copy of `$PLAN` at `origin/dev` `73104693`. It
printed `applied 2 edits`, and a second run exited 1 with `expected 1 match, found 0`.

```python
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()

old1 = (
    "- **UX overrides:** anything an end chat user experiences (leaked reasoning, wrong\n"
    "  citations, overflow apologies, silently stale knowledge base) is highest priority\n"
    "  regardless of track.\n"
)
new1 = (
    "- **UX overrides:** anything an end chat user experiences (leaked reasoning, wrong\n"
    "  citations, overflow apologies, silently stale knowledge base) is highest priority\n"
    "  regardless of track. One exception (2026-10-09): an operator decision moved\n"
    "  [#347](https://github.com/fasrc/archi/issues/347) (a literal `</think>` truncates an\n"
    "  answer) from `v2026.09.0` to `v2026.11.0` so that `v2026.09.0` can ship.\n"
)
old2 = (
    "or a first-tag-only rule — an open design question — so August is not feasible and\n"
    "September is the earliest that is. #326, #327 and #330 into `v2026.11.0`, each gating\n"
)
new2 = (
    "or a first-tag-only rule — an open design question — so August is not feasible and\n"
    "September is the earliest that is. On 2026-10-09 an operator decision moved #347 to\n"
    "`v2026.11.0` so that `v2026.09.0` can ship; Method records it as the one exception to\n"
    "the UX override. #326, #327 and #330 into `v2026.11.0`, each gating\n"
)
for old, new in ((old1, new1), (old2, new2)):
    count = text.count(old)
    if count != 1:
        sys.exit(f"expected 1 match, found {count}: {old[:60]!r}")
    text = text.replace(old, new)
path.write_text(text)
print("applied 2 edits")
```
