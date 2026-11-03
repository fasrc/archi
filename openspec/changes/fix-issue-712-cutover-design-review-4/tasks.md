Rules for every task:
- This is a **docs-only** change. Change only
  `docs/superpowers/specs/2026-10-10-vector-store-rebuild-cutover-design.md` (called
  `$DOC` below) and this folder's `tasks.md` ticks. No code, test, or config edit.
- Read `design.md` here (D1-D5) before task 1.1. It gives each edit site and the
  required text. Copy that text. Keep the doc's style: ASD-STE100 short sentences,
  section numbers, and links that pin a commit.
- Do not change the 4.10 table row `building | gate | no`, or the first-install condition
  of the baseline row (4.2). The #671 implementation plan owns those 2 findings.
- Set `DOC=docs/superpowers/specs/2026-10-10-vector-store-rebuild-cutover-design.md` in
  each shell.
- Run `bash scripts/gate.sh` before each commit, and commit only when it is green. The
  commit hook runs the gate too. Never use `--no-verify`. Add no `Co-Authored-By` or
  session trailers. Commit messages are short and lowercase.
- A gate failure in `tests/unit/test_judge_slurm.py` is the known flake #797 (this diff
  touches no code). Run the commit again. Do not edit that test.
- Never write a scratch file or a PR body under the repository (no file under `docs/`).
  Use `/tmp`.

## 1. The lifecycle: findings 1 and 2

- [x] 1.1 Confirm the old state first: `grep -n "Returns at once if this process already
      has a lifecycle connection" "$DOC"` prints line 396, and `grep -c "sync_pending"
      "$DOC"` prints `0`. Then apply design D1 (finding 1, P1: 5 edit sites in 4.10,
      4.5, 4.6, section 5, and section 6) and design D2 (finding 2: 9 edit sites in 4.2,
      4.3 step 1, 4.6, the 4.10 table, the 4.10 "Locks" paragraph, 4.10 recovery step 3,
      section 5, and section 6). Then confirm: the first grep prints nothing;
      `grep -n "sync_pending" "$DOC"` prints the 4.2 `phase` comment, the 4.10 table row,
      and a section 6 line; `grep -c "non-NULL" "$DOC"` prints `0`; and
      `git diff --stat origin/dev -- . ':!docs/superpowers/specs/2026-10-10-vector-store-rebuild-cutover-design.md' ':!openspec/changes/fix-issue-712-cutover-design-review-4'`
      prints nothing. Read sections 4.3 step 1, 4.5, 4.6, 4.10, and 5 once end to end,
      and fix any sentence that now contradicts the new rules. Tick this box. Run
      `bash scripts/gate.sh` and commit when green (message:
      `cutover design: owner runs its own retries, durable final sync (#712)`).

## 2. The build identity: findings 3 and 4

- [ ] 2.1 Confirm the old state first: `grep -c "source_digest" "$DOC"` prints a number
      above 0, and `grep -c "embedding_options" "$DOC"` prints `0`. Then apply design D3
      (finding 3: 7 edit sites in 4.2, 4.4, 4.7, and section 6), design D4 (finding 4: 8
      edit sites in 4.2, 4.3 step 2a, 4.7, 4.8, and section 6; its baseline edit is D3
      site 2), and design D5 (the revision note on line 4). Before you add the two new
      code links of D4 site 1, confirm the anchors on `e83160e6`:
      `git show e83160e6:src/cli/templates/base-config.yaml | sed -n 332,334p` shows
      `encode_kwargs:` and `normalize_embeddings:`, and
      `git show e83160e6:src/data_manager/vectorstore/postgres_vectorstore.py | sed -n 131,135p`
      shows the `cosine`, `l2`, and `inner_product` operators. Then confirm:
      `grep -c "source_digest" "$DOC"` prints `0`;
      `grep -n "differs from the \`source_digest\` of the live row" "$DOC"` prints
      nothing; `grep -n "normalize_embeddings\|encode_kwargs" "$DOC"` prints the 4.2
      identity rule and the section 6 lines; and the `git diff --stat` command of task
      1.1 prints nothing. Read sections 4.2, 4.3 step 2a, 4.4, 4.7, and 4.8 once end to
      end, and fix any sentence that now contradicts the new rules. Tick this box. Run
      `bash scripts/gate.sh` and commit when green (message:
      `cutover design: per-source shrink check, embedder options in the identity (#712)`).

## 3. Verify, push, and open the PR

- [ ] 3.1 Run the acceptance checks of issue #712 and keep their output for the PR body:
      `grep -n "Returns at once if this process already has a lifecycle connection" "$DOC"`
      (prints nothing), `grep -n "sync_pending" "$DOC"`,
      `grep -n "normalize_embeddings\|encode_kwargs" "$DOC"`,
      `grep -n "differs from the \`source_digest\` of the live row" "$DOC"` (prints
      nothing), and the `git diff --stat` command of task 1.1 (prints nothing). Also run
      `openspec validate fix-issue-712-cutover-design-review-4 --strict` (valid). If a
      check fails, fix the doc in this task first. Then tick this box, commit the tick
      through the hook (message: `mark #712 tasks complete`), and push with
      `git push -u origin fix/issue-712-cutover-design-review-4`. Write the PR body to
      `/tmp/pr-712-body.md`. The body must hold, in this order: the line
      `Closes #712`; one short paragraph for each of the 4 findings (the rule, the
      sections it changes, and the commit SHA of task 1.1 or 2.1); a "Choices" list with
      the 3 points that differ from the issue's plan text (D3: a removed or changed entry
      skips the check, not only a strict subset, and why; D4: values, not a digest, and
      why; D4: the key path `encode_kwargs.normalize_embeddings`, and the 4.3 step 2a
      edit); a note that the 2 findings in the #671 comment of 2026-10-15 are not
      changed; a note that `openspec/changes/fix-issue-712-cutover-design-review-4/` is
      the nightly driver folder and the only file outside the design doc; and the
      acceptance check output. If
      `gh pr list --repo fasrc/archi --head fix/issue-712-cutover-design-review-4 --state open`
      shows a PR, run `gh pr edit <number> --repo fasrc/archi --body-file /tmp/pr-712-body.md`.
      Otherwise run `gh pr create --repo fasrc/archi --base dev --head fix/issue-712-cutover-design-review-4 --title "docs(#712): fix 4 round-4 defects in the #671 cutover design" --body-file /tmp/pr-712-body.md`.
      Confirm the link with
      `gh pr view <number> --repo fasrc/archi --json closingIssuesReferences -q '.closingIssuesReferences[].number'`
      (prints `712`). Then post one reply on each of the 4 Codex threads of PR #711,
      with `gh api --method POST repos/fasrc/archi/pulls/711/comments/<id>/replies -f body="..."`.
      Each reply names the fix commit SHA and the new PR, in one or two sentences:
      `4287343449` (finding 1, the SHA of task 1.1), `4287343452` (finding 2, task 1.1),
      `4287343440` (finding 3, task 2.1), and `4287343430` (finding 4, task 2.1). If a
      push, a PR write, or a reply fails with HTTP 403 or "Resource not accessible",
      leave the branch pushed, open no PR in any other repository (no fork), write the
      exact error and the list of replies not posted to `STATUS.md`, and stop.
