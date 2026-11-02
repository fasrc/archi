## 1. Deploy README: the services list and the dev Slack tokens (red check and fix in one task)

- [x] 1.1 Run the three `grep` commands of design D3 against `deploy/scripts/README.md`
      and confirm that each prints NOTHING (the red check; no commit for this step).
      Then, in the same task, edit `deploy/scripts/README.md` only:
      (a) insert the "Services" bullet of design D1 in the "## Notes" list, directly
      before the "Secrets" bullet; (b) replace the "Secrets" bullet with the text of
      design D2. Copy the text of D1 and D2 as written; change nothing else in the file.
      Re-run the three `grep` commands and confirm each prints at least one line. Then
      confirm every scenario in
      `openspec/changes/fix-issue-714-dev-slack-tokens/specs/deploy-services/spec.md`:
      `grep -nE "SLACK_(BOT|APP)_TOKEN *=" deploy/scripts/README.md` and
      `grep -nE "lib\.sh:[0-9]" deploy/scripts/README.md` both print nothing, and the
      scope check of design D3
      (`git diff --name-only origin/dev -- . ':(exclude)openspec/changes/fix-issue-714-dev-slack-tokens'`)
      prints exactly `deploy/scripts/README.md`. Do not edit `lib.sh`, any test, or any
      config file. Run `bash scripts/gate.sh` and commit only when it is green
      (message: `docs(deploy): name the dev slack tokens and the services list`).

## 2. Publish

- [x] 2.1 Push the branch:
      `git push -u origin fix/issue-714-dev-slack-tokens`. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-714-dev-slack-tokens`
      with the title `docs(deploy): name the dev slack tokens and the services list` and
      `Closes #714` in the BODY (not the title). Pass the body with `--body` or a file
      under `/tmp`; do NOT commit a PR-body file to the repository. In the body, copy the
      table of `proposal.md` "## Why", paste the output of the four commands of design D3,
      and add one line: "The Codex thread on #644 (r4233054823) still needs a reply that
      names the fix commit." STOP at the open PR — do not merge.
