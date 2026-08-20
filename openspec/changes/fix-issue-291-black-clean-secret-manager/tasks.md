## 1. Confirm the premise and record the red state

- [x] 1.1 No commit in this task — it measures and prepares only. Do all of the following and
      record every number in the next task's commit body.
      **(a)** Confirm the module is still dirty on this branch:
      `black --check --diff src/cli/managers/secrets_manager.py | grep -c '^[+-]'`
      should print a churn count near **81**. If it prints 0, the file was already reformatted
      upstream — stop and report, this change is moot.
      **(b)** Repoint the gitignored root `tasks.md` symlink at this change's task list:
      `ln -sfn openspec/changes/fix-issue-291-black-clean-secret-manager/tasks.md tasks.md`. On this
      branch it dangles into another branch's change directory, and while it dangles
      `tests/unit/test_python_version_declaration.py::test_every_page_stating_a_minimum_is_guarded`
      fails with `FileNotFoundError`. That failure is environmental. Do **not** edit that test.
      Confirm `git status --porcelain` stays empty (the symlink is ignored via `.gitignore:64`).
      **(c)** Record the red state. Apply the reformat to a scratch copy — do not commit it —
      and measure patch coverage of the reflow with no new tests:
      `python -m pytest tests/unit/ -q --cov=src --cov-report=xml` then
      `diff-cover coverage.xml --compare-branch=origin/dev`.
      Expect roughly **50%** (8 of 16 measurable lines), i.e. below the 80% floor. This is the
      red state this change exists to clear. Then restore the file with
      `git checkout -- src/cli/managers/secrets_manager.py`.
      **(d)** Confirm isort is a no-op on the module (`isort --check` exits 0 already). If it
      is not, note it — isort reorders imports, which *would* change the syntax tree, and the
      AST proof in task 3 must then be taken across the black step alone.

## 2. Characterization tests for the members the reflow touches

- [x] 2.1 Create `tests/unit/test_secret_manager_provisioning.py` covering the four members.
      **The filename uses `secret` singular on purpose.** `.gitignore:19`'s `*secrets*` rule
      matches any path component containing `secrets`, so `test_secrets_manager_*.py` is
      unaddable: `git add` skips it with a hint and the commit then contains nothing. Before
      creating the file, confirm `git check-ignore -v --no-index <path>` reports no match, and
      after committing confirm the file is actually in the tree
      (`git show --stat HEAD`). Never work around this with `git add -f` (design.md,
      Decision 7). The four members are
      that hold the uncovered reflowed lines. Open with a module docstring saying these are
      characterization tests: they pin behaviour that exists today so that a whole-file black
      reflow has something to be measured against, and they are expected to pass **before** the
      reformat. A test here that fails before the reformat has encoded the wrong behaviour and
      is a bug in the test, not a finding.
      Build the manager against a real `.env` written into `tmp_path` (the constructor raises
      `FileNotFoundError` for a missing path), and pass a stub `config_manager` exposing
      `get_models_configs()` and `get_configs()`.
      Cover:
      - `_get_model_based_secrets` (lines 87, 90, 95, 100) — a models config naming an `OpenAI`
        model and an `Anthropic` model yields both keys; a section whose value is not a mapping
        is skipped without raising; a `HuggingFace`/`Llama`/`VLLM` name adds no key and logs the
        not-enforced warning (assert via `caplog`).
      - `write_secrets_to_files` (lines 180, 184) — each secret lands in
        `<target_dir>/secrets/<lowercased>.txt` holding only its value, and a `.env` is written
        alongside; a secret absent from the loaded `.env` raises `ValueError` naming it.
      - `write_env_file` (line 195) — writes one `NAME=value` line per resolvable secret and
        silently skips the rest.
      - `get_env_file_path` (line 209) — returns the loaded path.
      Run the suite and confirm it is green **with the module still unformatted**. Then run the
      pre-commit gate (`gate.sh` under `scripts/`, wired as the hook — never `--no-verify`) and
      commit as `test(secrets): cover the members a black reflow will touch`, with the task-1
      measurements in the body. Stage only the new test file. This is a test-only patch, so
      `diff-cover` reports no coverable lines and the floor is not in play.

## 3. The reformat

- [x] 3.1 Capture the module's syntax tree, reformat, and prove nothing moved:
      ```
      python -c "import ast,pathlib;print(ast.dump(ast.parse(pathlib.Path('src/cli/managers/secrets_manager.py').read_text())))" > /tmp/ast_before.txt
      black src/cli/managers/secrets_manager.py && isort src/cli/managers/secrets_manager.py
      python -c "import ast,pathlib;print(ast.dump(ast.parse(pathlib.Path('src/cli/managers/secrets_manager.py').read_text())))" > /tmp/ast_after.txt
      diff /tmp/ast_before.txt /tmp/ast_after.txt && echo "AST IDENTICAL"
      ```
      `diff` must be empty. If it is not, the change is not formatting-only — stop and report
      rather than adjusting the proof. Do **not** substitute `git diff -w` for this: `-w`
      ignores whitespace within a line but not black's re-wrapping, so it reports differences on
      a correct reformat (see design.md, Decision 1).
      Then confirm `black --check` and `isort --check` both exit 0 on the module, run the full
      unit suite and check the counts match task 1's baseline, and measure patch coverage
      explicitly:
      `python -m pytest tests/unit/ -q --cov=src --cov-report=xml` then
      `diff-cover coverage.xml --compare-branch=origin/dev` — the reflowed lines are now covered
      by task 2, so expect ~100% and at minimum ≥80%.
      Do not touch `pyproject.toml`: editing `requires-python` retargets black and reformats the
      whole repo. Stage only `src/cli/managers/secrets_manager.py`; the gate formats the whole
      repo, so after committing confirm `git status --porcelain` is empty rather than assuming
      no other file was rewritten. Run the gate and commit as
      `style(secrets): reformat to black`, with a body stating that this unblocks behavioural
      edits that `diff-cover` would otherwise reject, and recording the before/after churn and
      patch-coverage numbers.

## 4. Push and open the PR

- [ ] 4.1 Push with an explicit upstream — this branch was created from `origin/dev` and so
      currently tracks the trunk: `git push -u origin fix/issue-291-black-clean-secret-manager`.
      Open the PR against `dev` with an explicit head:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-291-black-clean-secret-manager`.
      Write the body with `--body-file`, and make sure it contains:
      - `closes #291` — the keyword links the issue only from the **body**; a closing keyword in
        the title does nothing. After creating, verify the link resolved rather than assuming it.
      - the measured numbers: 81 lines of black churn before, patch coverage 50% → ~100% after,
        and the unit-suite counts before and after.
      - the AST-equality proof as the formatting-only evidence, and an explicit note that
        `git diff -w` is **not** empty on this PR and why that is expected.
      - a call-out that the PR touches **two** files, not the one that issue #291's acceptance
        criterion 5 asks for, with the reason: a one-file reformat scores 50% patch coverage and
        the gate refuses it at 80%. Behaviour is unchanged; only the file count departs.
      Do not merge. A human reviews and merges in daylight.

## 5. Close the round-1 findings from the adversarial review of PR #308

- [x] 5.1 `model: opus` — Verify both findings against the code before acting. Confirm the
      CI/local gate split at `scripts/gate.sh:70` (directory args, walk skips the file) versus
      `:78` (explicit changed paths, reflows it), and confirm each of the three test gaps by
      reading the code path rather than the reviewer's summary.
- [x] 5.2 `model: opus` — File the CI-visibility gap as its own issue (#313) with both candidate
      fixes and the trade-off, rather than narrowing `.gitignore:19` inside a formatting-only PR.
- [x] 5.3 `model: opus` — Amend the spec delta so it states the enforcement gap and pins it with
      its own scenario. Re-run `openspec validate --strict`.
- [x] 5.4 `model: opus` — Close the three characterization gaps, then prove each has teeth
      by mutating the source and confirming that test alone turns red. Restore the source and
      confirm `git diff` on it is empty.
- [x] 5.5 `model: opus` — Re-run the gate, confirm patch coverage is still at or above the floor,
      and post the round log on the PR.
