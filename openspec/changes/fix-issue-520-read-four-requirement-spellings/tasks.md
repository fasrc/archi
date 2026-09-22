## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-520-read-four-requirement-spellings` exists, cut from
      `origin/dev` at `376b5867`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-520-read-four-requirement-spellings --strict` passed on
      the host. The `openspec` CLI is **not usable in this container** — do not run it, and do
      not add a task that does. It is already green.
- [x] 0.3 The red is measured at `376b5867` (Python 3.11.15, pip 26.1.2). All four findings
      reproduce. Module baseline: **183 passed in 0.20s**. The five monitored files each print
      `[] {} {}`, so all four findings are latent and the red test you write first is the whole
      proof.
- [x] 0.4 `design.md` D1 records a **correction to the issue body**. The issue says pip
      *"yields the buffered requirement, then the comment"* as two lines. Measured, pip
      **appends** the comment to the open buffer and flushes one logical line. Follow D1, not
      the issue text. You are not searching for an approach — every regex and every edge is
      already measured in `design.md`.

## Rules that apply to EVERY task below

- All work is in `tests/unit/test_base_image_dependency_compatibility.py`. **No `src/` file
  changes in this change.** Do not touch `_COMMENT`, `_PIN_PATTERN`, `_REQUIREMENT_PATTERN`,
  `_ARCHIVE_SUFFIX`, or which files the module reads (design D5).
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit, so
  a task that ends with the suite red can never be committed and the loop halts there.
- Before you add a test, `grep -n 'def test_' tests/unit/test_base_image_dependency_compatibility.py`
  and pick a name that is not already used. The gate runs no linter, so a duplicate
  `def test_...` silently replaces the older test while the suite stays green.
- After you insert a test, `git diff` the file and **read the trailing context line**.
  Inserting inside an existing test body instead of after it silently steals that test's last
  assertion. Confirm the test above yours still ends with its own `assert`.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/unit/test_base_image_dependency_compatibility.py | grep -c '^-.*def test_'`
  must print `0`.
- Run `black` and `isort` on the file **before** `git add`. The pre-commit hook's black is a
  writer while CI's is an assert, so a commit can be pushed misformatted. After each commit,
  `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer.
- Do not write any file under `docs/`. The PR body goes in `/tmp`, never in the repo.
- Each new regex or loop carries a comment naming the pip rule it mirrors and the review that
  found the gap, matching the module's existing comment style.
- Where a verdict depends on pip, measure pip
  (`from pip._internal.req.constructors import install_req_from_line`,
  `from pip._internal.req.req_file import join_lines, COMMENT_RE, SUPPORTED_OPTIONS_REQ`)
  and record the measured value in the test's docstring. Do not hand-roll PEP 440 or PEP 508.
- After every task, the five monitored files must still print `[] {} {}`. The loop in
  `## Commands` below checks this; run it each time.

## 1. Finding 1 — a comment line never continues (hiding defect)

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add a test to `TestOpaqueRequirementsFailClosed`. Assert that
      `_opaque_requirements("# comment \\\nvllm-0.9.0-py3-none-any.whl\n")` equals
      `["vllm-0.9.0-py3-none-any.whl"]`. Add the indented spelling (`   # comment \`) and the
      bare-hash spelling (`#\`) as cases. Add one case that must NOT change: an **inline**
      comment ending in a backslash, `vllm==0.9.0  # note \` followed by `numpy==2.0.0`, still
      joins into one logical line and still reads the pin `vllm==0.9.0` — see design D1's
      measured table. Run
      `python -m pytest tests/unit/test_base_image_dependency_compatibility.py -q --no-header`
      and confirm the comment-continuation cases FAIL on their assertion, not on an import or
      collection error.

      **Then green.** In `_joined_lines` (`:295`), test each physical line against `_COMMENT`
      (`:379` — the same regex as pip's `COMMENT_RE`) before treating a trailing backslash as
      a continuation. Two rules, both from design D1:
      a line `_COMMENT.match(...)` matches never opens a buffer; a line it matches that
      arrives while a buffer is open is appended to the buffer and the buffer is flushed as one
      logical line. Key on `_COMMENT.match(...)`, never on `"#" in line`. Keep the existing
      join mechanics — `raw_line[:-1]`, which keeps the whitespace before the backslash.

      Update `_joined_lines`' docstring to name the comment rule and the review that found the
      gap, dated 2026-09-22, in the style the surrounding docstrings use.

      Then: format, `git add`, `bash scripts/gate.sh` exits 0, commit
      `fix(#520): stop a comment line continuing onto the requirement after it`.

## 2. Finding 3 — the conditional-pin reader reads joined lines (hiding defect)

- [x] 2.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add a test to `TestProtectedPinsAreUnconditional`. Assert that
      `_conditional_protected('vllm==0.9.0 \\\n; python_version < "3.11"\n')` equals
      `{"vllm": 'python_version < "3.11"'}`, and that `_parse_pins` on the same text still
      returns `{"vllm": "0.9.0"}`. Confirm the first assertion fails (it returns `{}` today)
      and the second already passes.

      **Then green.** In `_conditional_protected` (`:581`), iterate `_joined_lines(text)`
      instead of `text.splitlines()`. Change nothing else in the loop — its `startswith("#")`
      and `startswith("-")` skips keep working. Do **not** change its `line.split("#", 1)[0]`
      comment cut; design D5 records why that asymmetry is out of scope.

      Then: format, `git add`, gate green, commit
      `fix(#520): read a continued environment marker in the conditional-pin guard`.

## 3. Finding 4 — pip's short `-C` option is cut (false positive)

- [x] 3.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add a test to `TestCompactOptionFormsAreRecognized`. For each of
      `vllm==0.9.0 -Cfoo=bar`, `vllm==0.9.0 -C foo=bar` and `vllm==0.9.0 \` + `    -Cfoo=bar`,
      assert `_unpinned_protected(text) == {}` **and** `_parse_pins(text) == {"vllm": "0.9.0"}`.
      Both assertions fail today — the pin reader records nothing and the unpinned check
      reports vllm. Also assert the long spelling `--config-settings`, which already passes,
      keeps passing. Record in the docstring that pip 26.1.2's `SUPPORTED_OPTIONS_REQ` carries
      exactly `--hash` and `-C`/`--config-settings`, measured, so `-C` is its only short form.

      **Then green.** Widen `_PER_REQUIREMENT_OPTION` (`:386`) from `\s+--\S+.*$` to
      `\s+(?:--\S+|-C(?:\s|\S)).*$` or an equivalent. The `(?:\s|\S)` is deliberate: pip's
      option takes a value, so a bare trailing `-C` is not cut. Add a case asserting a bare
      trailing `-C` is not cut. One edit repairs both readers, because `_parse_pins` and
      `_parse_requirements` share `_requirement_lines` (design D4) — do not edit either reader.

      Then: format, `git add`, gate green, commit
      `fix(#520): cut pip's short config-settings option from a requirement line`.

## 4. Finding 2 — a detached extras list is not an archive (false positive)

- [x] 4.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add a test to `TestOpaqueRequirementsFailClosed` (a different name from
      task 1's). Assert `_opaque_requirements` returns `[]` for `example.zip [foo] ==1.0` and
      for `example.tar [foo] (>=1)`. Assert in the SAME test that `example.zip [foo]` and
      `example.zip` are each still reported — those two negatives are what proves the widening
      is not too wide (design D7). Confirm the first two fail and the last two already pass.

      **Then green.** In `_PATH_OR_ARCHIVE_REQUIREMENT` (`:426`), allow an optional
      `\s*\[[^\]]*\]\s*` between `_ARCHIVE_SUFFIX` and the `_COMPARISON_AFTER_SUFFIX`
      lookahead, reusing the `_ATTACHED_EXTRAS` shape (`:425`). Keep the widening inside this
      one pattern, as `_NAME_WITH_SPACED_EXTRAS` (`:448`) did for `@`, so no pin is read across
      a space.

      Then: format, `git add`, gate green, commit
      `fix(#520): exempt a detached extras list in front of a comparison`.

## 5. Prove the whole thing, then publish

- [ ] 5.1 Run the acceptance oracle and confirm every number. Write the probe from the issue
      body to `/tmp/probe520.py` and run it from the repo root as
      `python3 - < /tmp/probe520.py`. It must print `0 checks give the wrong verdict`.
      **Caution:** `_joined_lines` is a generator — if you add a check of your own, compare
      `list(_joined_lines(text))`, never the bare call, or you get a mismatch that is an
      artefact of the probe (design D9). Then confirm all of:
      the monitored-files loop prints one line per file, each ending in `[] {} {}`;
      `python -m pytest tests/unit/test_base_image_dependency_compatibility.py -q` reports at
      least **187 passed** and **0 failed**;
      the deleted-test count prints `0`;
      `bash scripts/gate.sh` exits 0;
      `git status --porcelain` is empty.
      Commit only if something changed; otherwise this task commits nothing and you move on.

- [ ] 5.2 Push the branch and open the PR. The branch was cut with `checkout -b`, so its
      upstream is `origin/dev` — push with
      `git push -u origin fix/issue-520-read-four-requirement-spellings` to repoint it.
      Confirm the push landed on **fasrc/archi**, not a fork:
      `git ls-remote --heads origin fix/issue-520-read-four-requirement-spellings` must print
      the same SHA as `git rev-parse HEAD`.
      Write the PR body to `/tmp/pr-body-520.md` — **never** under `docs/` — and include the
      literal line `Closes #520`. Then
      `gh pr create --repo fasrc/archi --base dev --title "fix(#520): read four more requirement spellings as pip does in the base-image dependency guard" --body-file /tmp/pr-body-520.md`.
      A `Closes #520` in the title does not link the issue; it must be in the body. Verify the
      link: `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences` must list 520.
      If it does not, edit the body and re-verify.

- [ ] 5.3 Reply in the four threads on PR #506 — 4058046837, 4058046838, 4058046843 and
      4058046844 — each with the SHA of the commit that closes it (tasks 1, 4, 3 and 2
      respectively). Use the review-comment reply API, not a new top-level comment. Do not use
      `gh pr comment --json`; that flag is unsupported and the call fails before posting.
      Then STOP. Do not merge this PR, and do not merge #506. A human merges, in daylight.

## Commands

```bash
# the module's own suite — 183 passed at 376b5867, at least 187 when done
python -m pytest tests/unit/test_base_image_dependency_compatibility.py -q --no-header

# the monitored files must print [] {} {} after every task
python3 -c "
import sys; sys.path.insert(0,'.')
import tests.unit.test_base_image_dependency_compatibility as g
for p in (g.CPU_HEADER, g.GPU_HEADER, g.BASE_REQUIREMENTS, g.GENERATED_CPU_REQUIREMENTS, g.GENERATED_GPU_REQUIREMENTS):
    if p.exists():
        t = p.read_text(); print(p.name, g._opaque_requirements(t), g._unpinned_protected(t), g._conditional_protected(t))
"

# no test was deleted — must print 0
git diff origin/dev -- tests/unit/test_base_image_dependency_compatibility.py | grep -c '^-.*def test_'

# the gate, before every commit
bash scripts/gate.sh
```
