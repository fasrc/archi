## 1. Close the fail-open hole (TDD)

- [x] 1.1 Write the directive tests red, then make them green — **in this one task**,
      because the gate runs before every commit and a task that ends with the suite red can
      never be committed. All work is in `tests/unit/test_requirements_hygiene.py`; no
      `src/` file changes in this change.
      **Red first:** add a new parametrized test
      `test_install_directives_are_flagged` asserting
      `declares_unreadable_requirement(line)` is True for at least:
      `-e git+https://host/duckdb.git#egg=duckdb`,
      `--editable git+https://host/duckdb.git`,
      `--editable=git+https://host/duckdb.git`,
      `-e ./vendor/duckdb`,
      `-e./vendor/duckdb` (attached short form),
      `-r extra-requirements.txt`,
      `--requirement=extra-requirements.txt`,
      `-c constraints.txt`,
      `--constraint=constraints.txt`;
      and a companion assertion (same test or a sibling) that
      `requirement_project_name(line)` is `None` for each — a directive names no readable
      project. **Move** the `-r requirements-base.txt` case out of
      `test_readable_requirement_shapes_are_not_flagged`'s parametrize list — that entry
      encodes the fail-open behaviour this change removes (design D4). Leave the same
      string in `test_non_duckdb_declarations_are_not_detected` untouched; it still passes.
      Run `python -m pytest tests/unit/test_requirements_hygiene.py -q` and confirm the
      new cases FAIL on the assertion (currently False), not on an import error.
      **Then green:** in `_requirement_body()`, before the blanket hyphen skip, return the
      line text for install directives so they flow into the existing unreadable-shape path
      (design D3) — detect them with a module-level compiled pattern per design D1
      (`-e`/`-r`/`-c` with space, `=`, attached value or end-of-line; `--editable`,
      `--requirement`, `--constraint` with space, `=` or end-of-line; nothing else).
      Update the module docstring, `_requirement_body`'s docstring and
      `declares_unreadable_requirement`'s docstring to state the actual coverage: inert
      options are skipped, install directives fail closed, includes are reported rather
      than followed (design D2). Re-run the file's suite: everything green — the five
      monitored files carry only inert `--extra-index-url` lines, so
      `test_requirements_files_declare_only_readable_requirements` stays green on the
      clean tree.

- [x] 1.2 Prove the guard fires end-to-end by planting and reverting (issue #253
      acceptance criterion 2). Append three lines to
      `requirements/requirements-base.txt`:
      `-e git+https://host/duckdb.git#egg=duckdb`, `-e ./vendor/duckdb`,
      `-r extra-requirements.txt`. Run
      `python -m pytest tests/unit/test_requirements_hygiene.py -q` and capture the
      FAILING output — `test_requirements_files_declare_only_readable_requirements` must
      name all three planted lines with their line numbers. Revert with
      `git checkout -- requirements/requirements-base.txt`, re-run, capture the PASSING
      output. Both captures go in the PR body. Confirm `git status --porcelain` shows only
      the intended change files afterwards.

## 2. Verify against the issue's acceptance criteria

- [x] 2.1 Run `bash scripts/gate.sh` **bare — no pipe, no redirect** (it refuses to run
      when its output is piped or redirected). Format, lint and the unit suite must pass.
      Expect diff-cover to report **no lines with coverage information**: this diff is
      tests-only (plus OpenSpec markdown), so there are no measurable `src/` lines. That
      is a legitimate pass, not a bypassed gate. Never `--no-verify`.
- [x] 2.2 Run `openspec validate fix-issue-253-duckdb-guard-pip-directives --strict` and
      confirm it passes.

## 3. Ship it (no merge)

- [x] 3.1 Push with `git push -u origin fix/issue-253-duckdb-guard-pip-directives` — the
      branch was created from `origin/dev` and tracks the trunk until `-u` repoints it.
- [x] 3.2 Open the PR: `gh pr create --repo fasrc/archi --base dev`. The **body** MUST
      contain `closes #253` — a closes-keyword in the title does not link the issue. The
      body must also record: the red-then-green evidence from 1.1; the plant-and-revert
      evidence from 1.2; that the `-r requirements-base.txt` parametrize case moved from
      the not-flagged list to the flagged side and why (design D4); that includes are
      reported rather than followed recursively and why (design D2); and that diff-cover
      reports no measurable lines because the diff is tests-only. **Never merge** — a
      human merges in daylight.

## 4. Close the round-2 holes Codex review found on PR #298

- [x] 4.1 Reproduce all three claims against pip 26.1.2 itself before accepting them —
      call `join_lines`, `build_parser().parse_args` and `expand_env_variables` from
      `pip._internal.req.req_file` directly, and run the PR-head guard over a
      `requirements-base.txt` with the three shapes planted. Record that the head guard
      reports zero unreadable lines for all three.
- [x] 4.2 Refactor first, green to green: extract the file scan out of
      `test_requirements_files_declare_only_readable_requirements` into
      `unreadable_requirement_lines(text)`, so the read path can be tested against
      synthetic content instead of a planted file. Suite stays at 43 passed.
- [x] 4.3 Write the round-2 cases red: `test_abbreviated_install_directives_are_flagged`,
      `test_variable_bearing_option_names_are_flagged`,
      `test_variables_in_inert_option_values_are_not_flagged`,
      `test_inert_options_stay_inert`, `test_logical_lines_joins_pip_continuations`,
      `test_continued_install_directives_are_flagged_at_the_first_line` and
      `test_a_continuation_cannot_hide_a_duckdb_name`. Confirm 15 fail — 13 on the
      assertion, 2 on the not-yet-written `logical_lines`.
- [x] 4.4 Make them green: widen `_INSTALL_DIRECTIVE_PATTERN` to every long-name prefix
      (design D6), fail closed on a `${VAR}` in an option name only (design D7), and add
      `logical_lines()` porting pip's `join_lines` (design D8). Update the module
      docstring, `_requirement_body`'s and `declares_unreadable_requirement`'s to state
      the new coverage and the guard's boundary.
- [x] 4.5 Re-run the plant-and-revert with the three new shapes
      (`--edit git+...duckdb`, `--require\` + `ment extra-requirements.txt`,
      `-${DIRECTIVE} constraints.txt`). Confirm all three are named with the right line
      numbers, then `git checkout -- requirements/requirements-base.txt` and confirm the
      suite is green and the tree carries only the intended change files.
- [x] 4.6 Run `bash scripts/gate.sh` bare and `openspec validate
      fix-issue-253-duckdb-guard-pip-directives --strict`.
- [x] 4.7 Reply in thread to each of the four Codex findings, and escalate the P1 —
      issue #253 carries the `parked` label, so whether this PR ships at all is a human
      scheduling decision, not one this change set can make.
