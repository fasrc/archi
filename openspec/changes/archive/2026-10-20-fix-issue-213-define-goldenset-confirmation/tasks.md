## Notes for every task

- This is a spec-only change (issue #213). Do not change code, tests, config, or the golden-set
  bank. The bank is in `fasrc/archi-config`, not in this repository. Do not comment on or edit
  anything in `fasrc/archi-config`.
- The requirement text in `specs/ragas-goldenset-maintenance/spec.md` is the operator's decision
  (#213 body, "Decision", 2026-10-19). Do not reword it. If it seems wrong, write the question to
  `docs/questions.md` and stop.
- The merge of this change's delta into `openspec/specs/ragas-goldenset-maintenance/spec.md` by
  `openspec archive` IS the deliverable of #213 (plan step 5, acceptance criterion 4). It is not
  an edit of a spec to match code, so the "do not modify specs" rule does not apply to it.
- Archive hazard: `openspec archive` moves this folder, and this `tasks.md` with it, to
  `openspec/changes/archive/<date>-fix-issue-213-define-goldenset-confirmation/`. The root
  `tasks.md` symlink then dangles, and `tests/unit/test_python_version_declaration.py` fails on it
  with `FileNotFoundError`, which blocks the commit. Task 1.1 repoints the symlink in the same
  turn. The symlink is gitignored, so the repoint does not change the tree.
- Each task is one commit. Commit messages are short and lowercase, with no `Co-Authored-By`
  trailer. Never use `--no-verify`.

## 1. Archive the change

- [x] 1.1 In ONE commit: (a) Run `openspec validate fix-issue-213-define-goldenset-confirmation --strict` and confirm that it passes (acceptance criterion 5 needs the validate BEFORE the archive). Put its output line in the commit body. (b) Run `openspec change show fix-issue-213-define-goldenset-confirmation --json --deltas-only` and confirm that there is one ADDED requirement, `Confirmation standard for locking`, that its requirement text contains `SHALL`, and that it has 5 scenarios. (c) Change this checkbox (1.1) to `[x]` now, before the archive moves this file. (d) Run `openspec archive fix-issue-213-define-goldenset-confirmation --yes`. (e) Repoint the symlink: `ln -sfn "$(ls -d openspec/changes/archive/*-fix-issue-213-define-goldenset-confirmation)/tasks.md" tasks.md`, then confirm that `test -e tasks.md` passes and that `python -m pytest tests/unit/test_python_version_declaration.py -q` is green. (f) Confirm that `grep -n "### Requirement: Confirmation standard for locking" openspec/specs/ragas-goldenset-maintenance/spec.md` prints exactly one line, that `grep -c "^#### Scenario: " openspec/specs/ragas-goldenset-maintenance/spec.md` is 5 more than on `origin/dev`, and that `openspec validate ragas-goldenset-maintenance --type spec --strict` passes. (g) Confirm that `git status --porcelain` shows only the main spec, the deleted `openspec/changes/fix-issue-213-define-goldenset-confirmation/` files, and the new archive folder (`.openspec.yaml`, `proposal.md`, `tasks.md`, `specs/ragas-goldenset-maintenance/spec.md`). Stage them with `git add -A openspec/`. Run `bash scripts/gate.sh` (it reports no coverage lines for a spec-only diff, which passes), and commit as `docs(openspec): archive the goldenset confirmation standard (#213)`.

## 2. Verify, push, and open the PR

- [ ] 2.1 Run `bash scripts/gate.sh` on the branch tip and confirm that it is green. Confirm that `git status --porcelain` is empty. Push with `git push -u origin fix/issue-213-define-goldenset-confirmation`. Open the PR with `gh pr create --repo fasrc/archi --base dev --head fix/issue-213-define-goldenset-confirmation --title "docs(openspec): define what confirmed means for a golden-set lock" --body-file <a file outside the repository>`. In the PR **body** (not the title), put `Closes #213`; list the six acceptance criteria of #213, each with its evidence (the `grep -n` line, the validate output from the task 1.1 commit body, the archive path, the gate result); state that the change is named `fix-issue-213-define-goldenset-confirmation` and not `define-goldenset-confirmation` because the nightly loop reads `openspec/changes/fix-issue-<n>-<slug>/tasks.md`; and state the after-merge step for a human: comment on `fasrc/archi-config#19` with a link to the merged requirement. Then change this checkbox (2.1) to `[x]` through the `tasks.md` symlink, commit as `chore: mark task 2.1 complete`, and push again. Confirm that `git rev-parse HEAD` equals `git rev-parse origin/fix/issue-213-define-goldenset-confirmation`. Do not merge.
