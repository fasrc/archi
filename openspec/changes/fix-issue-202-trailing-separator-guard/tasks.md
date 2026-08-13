Scope: ONE helper (`_resolve_totally`, `src/utils/goldenset_maintenance.py:285`) plus the two
call sites inside `resolve_persisted_path` (line 405), their docstrings, and their tests in
`tests/unit/test_goldenset_maintenance.py::TestPersistedDocumentPath` (class at line 1204).
Do not widen it. Closes fasrc/archi#202.

Every shell task starts with:
```bash
export PATH=/home/austin/miniforge3/envs/archi/bin:$PATH
```
Gate before every commit: `bash scripts/gate.sh`, run **bare** — no pipe, no redirect (the
gate refuses a piped invocation). Never `--no-verify`. No `Co-Authored-By` or other
AI-attribution trailers; short lowercase commit subjects. Run `black` and `isort` **before**
`git add`, not after: the pre-commit hook formats in place while CI only asserts, so a file
formatted after staging is committed unformatted. Verified already: both files are
black/isort-clean on `origin/dev`, so no reflow will collapse diff coverage.

Each numbered task below is one loop turn and ends **green and committable** — the failing test
and the code that makes it pass are deliberately in the same task, because a turn that ends with
the suite red can never clear the gate and would deadlock the loop.

## 1. Refuse a stored spelling the kernel cannot traverse (red, then green, one commit)

- [x] 1.1 Reproduce → red → green → gate → commit, in this order:

      **Reproduce.** Run the snippet in the issue's **Commands** section. Expect
      `Path('safe.md/') -> PosixPath('safe.md')`, `open('<root>/safe.md/')` raising
      `NotADirectoryError` errno 20, and the guard RETURNING `<root>/safe.md` for both
      `safe.md/` and `safe.md/.`. Save the exact output — the PR body must quote it (task 5.1)
      and the commit message body should carry the red test failure.

      **Red.** In `TestPersistedDocumentPath`, beside
      `test_a_loop_erased_by_parent_traversal_is_refused` (line 1342), add two tests: a real temp
      data root holding a real `safe.md`, asserting `resolve_persisted_path("safe.md/", str(root))`
      and `resolve_persisted_path("safe.md/.", str(root))` each raise `ValueError` whose message
      names the offending spelling. Match on the path and the reason, e.g.
      `pytest.raises(ValueError, match=r"'safe\.md/'.*cannot be resolved: Not a directory")`.
      Run `python -m pytest tests/unit/test_goldenset_maintenance.py::TestPersistedDocumentPath -q`
      and confirm both fail because the guard **RETURNED a path** — not from a fixture error, an
      import error, or a reason mismatch. Do NOT continue until the failure reason matches.

      **Green.** Give `_resolve_totally` a third parameter — the spelling to probe — defaulting
      to `str(path)` so the existing behavior of every current caller is unchanged, and pass it
      to `os.stat` (line 369) in place of `path`. Then in `resolve_persisted_path`, build the raw
      probe target from the **raw string**: the `file_path` itself when `os.path.isabs(file_path)`,
      otherwise `os.path.join(str(root), file_path)`. Use `os.path.isabs` on the raw string, not
      `candidate.is_absolute()`, and a string join, not `root / candidate` — a `pathlib` join
      re-erases the trailing separator and makes the whole fix a no-op (design.md Decision 4).
      Leave the resolved *output* coming from `Path.resolve()` exactly as today; only the probe
      input changes. Do NOT add a second raise site, do NOT add a new reason string (ENOTDIR
      arrives at the existing `except OSError` branch and reports as `Not a directory`), and do
      NOT touch the `..` erasure gate or the ENOENT tolerance.

      **Gate and commit.** Both new tests pass, then `bash scripts/gate.sh` bare, then commit —
      e.g. `fix(goldenset): probe the stored path spelling, not its normalized form`.

## 2. Prove the fix over-refuses nothing, and pin the data-root decision

- [x] 2.1 Add the discriminating tests (tests-only; still gate before committing):

      - the clean spelling still resolves: `resolve_persisted_path("safe.md", str(root))` returns
        the resolved path (this is an acceptance criterion, and it is what fails if the fix
        over-refuses);
      - traversable `.`/`..` spellings still resolve — `./safe.md`, `web/./a.md`, and
        `web/../web/a.md` with every component real — one test naming all three, asserting the
        returned path is `<root>/…` as expected. This pins design.md Decision 2: refusal is the
        kernel's verdict on the spelling, never a rule about which characters it contains;
      - **the data-root decision (the issue's step 4), pinned either way**: `data_path` spelled
        with a trailing separator (`str(root) + "/"`) still resolves a contained `file_path`, and
        is NOT refused. Comment the test with why the asymmetry is principled rather than a
        carve-out — `os.stat` accepts a trailing separator on a real directory and rejects it on
        a non-directory, so one probe rule serves both arguments (design.md Decision 3).

      Then run the whole containment set unmodified:
      `python -m pytest tests/unit/test_goldenset_maintenance.py -k "resolve or persisted or contain" -q`.
      Every pre-existing test MUST pass **without being edited** — especially
      `test_a_deleted_document_still_reaches_the_read_to_fail_there` (the ENOENT tolerance this
      change is forbidden to narrow), `test_a_loop_erased_by_parent_traversal_is_refused`, and
      `test_a_missing_component_erased_by_parent_traversal_is_refused` (whose reasons a
      name-based refusal would have shadowed). If any needed editing to pass, the fix changed
      behavior it should not have — stop and rethink; do not adjust the test.
      Gate bare, then commit.

## 3. Mutation-check the new tests, and document the reasoning in place

- [x] 3.1 Revert ONLY the probe change (keep every test), re-run
      `python -m pytest tests/unit/test_goldenset_maintenance.py -q`, and confirm **exactly** the
      two refusal tests from task 1.1 fail and nothing else — that is what proves they pin the
      new behavior rather than passing incidentally. Also revert only the string join back to
      `root / candidate` and confirm the same two tests fail, which is the drift this change is
      most exposed to. Restore the fix.

      Then extend the docstrings, in the existing voice, without restating design.md:
      `_resolve_totally` — that the probe's authority comes from receiving the pathname **as the
      row spelled it**, since `Path()` erases a trailing separator and `.` components in the
      constructor (`Path('safe.md/') -> PosixPath('safe.md')`), so a probe fed a constructed path
      reports on a pathname nobody stored; and `resolve_persisted_path` — why the probe target is
      composed with a string join while the returned path still comes from `Path.resolve()`, so
      the next reader does not "tidy" the join into a `pathlib` one and silently revert this.
      Gate bare, then commit.

## 4. Validate the spec and the whole suite

- [x] 4.1 Run `openspec validate fix-issue-202-trailing-separator-guard --strict` and confirm it
      passes. The delta is a `## MODIFIED Requirements` restatement of *"Persisted-document path
      resolution is total and contained"* — one requirement carrying the tightened clause and the
      new scenarios, never a second requirement contradicting the first. Note in the PR body that
      the base requirement still lives in the merged-but-unarchived
      `fix-issue-183-symlink-loop-containment` delta, so this change archives after it.

      Then the full local suite: `python -m pytest tests/unit/ -k goldenset -q` (includes
      `tests/unit/test_goldenset_maintenance_script.py`, which asserts the caller's
      "outside the data root" message reaches stderr — that path must be intact), followed by
      `bash scripts/gate.sh` bare one more time, confirming format, lint, tests, and ≥80% diff
      coverage on the changed lines. Commit any task-list progress.

## 5. Push and open the PR

- [x] 5.1 Push the branch with an explicit upstream — `git push -u origin fix/issue-202-trailing-separator-guard`
      — because the branch was created from `origin/dev` and therefore tracks the trunk, not
      itself. Then open the PR; the change is not done without it:
      `gh pr create --repo fasrc/archi --base dev --title "fix(goldenset): refuse a persisted path spelling the OS cannot traverse (closes #202)"`.
      Write the body to a file and pass `--body-file`. The body MUST contain `closes #202` in the
      **body** (a closes-keyword in the title does not link the issue), quote the reproduce output
      from task 1.1, state that refusal is by the kernel's verdict rather than by name and why
      (design.md Decision 2), record the `data_path` trailing-separator decision (Decision 3) and
      the test that pins it, and confirm that every pre-existing containment test passed
      unmodified and that the ENOENT tolerance is untouched.
      Link the first use of any project term to the glossary.

- [ ] 5.2 Request review on the PR. Do NOT merge — a human merges in daylight.
