## 1. Declare the real floor, driven by a test that fails first

- [x] 1.1 Add `tests/unit/test_python_version_declaration.py` and fix the declaration in the
  same task. **Red first, inside this task:** write the guard that reads `pyproject.toml`
  with `tomllib`, parses `project.requires-python` with
  `packaging.specifiers.SpecifierSet`, takes its lower bound, reads
  `tool.pyright.pythonVersion` from the same file, and asserts
  `floor >= Version(pythonVersion)`. Run
  `python -m pytest tests/unit/test_python_version_declaration.py -q` and confirm it FAILS
  for the right reason — the floor is `3.7`, the pyright target is `3.11`. Only then set
  `requires-python = ">=3.11"` in `pyproject.toml:5` and re-run to green. Do not split the
  red step into its own task: a task that ends with the suite red can never pass the gate,
  so the loop would deadlock before it could commit.
- [x] 1.2 Add the second guard to the same file: assert the **running** interpreter satisfies
  the declared specifier. Verify it can fail — temporarily set `requires-python = ">=3.99"`,
  watch that test go red, then revert to `>=3.11` and confirm the file is green again. End
  this task with the suite passing.
- [x] 1.3 Derive the floor from the parsed specifier rather than string-comparing `">=3.11"`,
  so a later `">=3.11,<4"` or `"~=3.11"` still reads as satisfied. Add a test that pins this:
  feed the comparison helper a bounded specifier and assert it is accepted.
- [x] 1.4 Run `black` on the new test file **before** `git add` — the pre-commit hook
  reformats after staging, so a file staged unformatted is committed unformatted and CI's
  `--check` then fails. Confirm `git status` is clean after committing. Run the gate bare —
  `bash scripts/gate.sh`, no pipe and no redirect — then commit.

## 2. Correct the one surviving stale claim in prose

- [x] 2.1 Change `- Python 3.7+` at `docs/docs/adding_providers.md:244` to state the declared
  floor. Confirm `grep -rn "Python 3\.7" CLAUDE.md docs/` returns nothing, and that
  `grep -rn "3\.7" pyproject.toml` returns nothing. Do **not** repair unrelated
  `api_reference.md` line anchors — that is issue #190 and belongs in its own PR.
- [x] 2.2 Run the gate bare and commit.

## 3. Validate and open the PR

- [x] 3.1 Run `openspec validate fix-issue-201-python-floor --strict` and confirm it passes.
- [x] 3.2 Push with `git push -u origin fix/issue-201-python-floor`. The `-u` matters: the
  branch was created with `git checkout -b ... origin/dev`, so its upstream is currently the
  trunk rather than its own remote branch.
- [x] 3.3 Open the PR against `fasrc/archi:dev` with
  `gh pr create --repo fasrc/archi --base dev`. Put `Closes #201` in the **body** — a
  closing keyword in the title does not link the issue. The body must carry the evidence
  that this one-line change is the *correct* line: the output of
  `grep -n "requires-python\|pythonVersion" pyproject.toml`,
  `grep -rEn "^\s+match .*:$" src/ --include=*.py`, the CI `python-version` pins, and
  `grep -rn "3\.7" pyproject.toml CLAUDE.md docs/`. State that the floor is `>=3.11` and not
  `>=3.10` because 3.10 is untested and unchecked (see design.md, Decision 1). Link
  https://github.com/fasrc/archi/pull/192#discussion_r3713501203 as the origin. Note that no
  `src/` file changes, so diff coverage reports no measurable lines — expected, not a skipped
  gate. **Never merge** — a human merges.
