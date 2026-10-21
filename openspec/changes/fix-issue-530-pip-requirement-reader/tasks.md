## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-530-pip-requirement-reader` exists, cut from `origin/dev` at
      `5564e016`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-530-pip-requirement-reader --strict` passed on the host.
      The `openspec` CLI is **not usable in this container** — do not run it, and do not add a
      task that does. It is already green.
- [x] 0.3 The red is measured at `5564e016` (Python 3.11.15, pip 26.1.2, packaging 26.2). Module
      baseline: **254 passed**. Every row in `proposal.md`'s table reproduces. The monitored
      files print 0 opaque lines and pin counts **1, 5, 105, 106, 110** (the issue body's
      1, 5, 104, 105, 109 are from an older tree — use these numbers).
- [x] 0.4 `prototype.md` is the measured reader. Appended to a copy of the module, it
      passes all 254 existing tests and every acceptance row. `design.md` D1 records two
      corrections to the issue body (`--hash <v>` is accepted; a non-dash token in the option
      half is ignored, not rejected). Follow the design, not the issue text, where they differ.
      You are not searching for an approach.

## Rules that apply to EVERY task below

- All work is in `tests/unit/test_base_image_dependency_compatibility.py`. **No `src/` file
  changes.** Do not touch `_joined_lines` (#529 owns it), any requirements file, or which
  files the module reads.
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit, so
  a task that ends with the suite red can never be committed and the loop halts there.
- Never import `pip._internal` in the module or in a test (design D8). Do not hand-roll PEP 440
  or PEP 508: `packaging` does it.
- Before you add a test, `grep -n 'def test_' tests/unit/test_base_image_dependency_compatibility.py`
  and pick a name that is not already used. The gate runs no linter, so a duplicate
  `def test_...` silently replaces the older test while the suite stays green.
- After you insert a test, `git diff` the file and **read the trailing context line**. An
  insert inside an existing test body silently steals that test's last assertion. Confirm the
  test above yours still ends with its own `assert`.
- **Never delete a test, and never weaken an existing assertion.** After each commit,
  `git diff origin/dev -- tests/unit/test_base_image_dependency_compatibility.py | grep -c '^-.*def test_'`
  must print `0`. If an existing test goes red, the code is wrong, not the test: compare with
  `prototype.md`, which keeps all 254 green.
- Run `black` and `isort` on the file **before** `git add`. After each commit,
  `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer.
- Do not write any file under `docs/`. The PR body goes in `/tmp`, never in the repo.
- After every task, run the monitored-files command in `## Commands`. It must print `[]` for
  opaque lines, the pin counts 1, 5, 105, 106, 110, and `{}` `{}` on every line.

## 1. Commit A — the option half (design D1, D4)

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add a test class `TestOptionHalfReadsAsPipDoes` after
      `TestCompactOptionFormsAreRecognized`. Parametrize:
      - rejected, assert `_parse_pins(text) == {}` and `"vllm" in _unpinned_protected(text)`:
        `vllm==0.9.0 -Cfoo=bar -Cbad`, `vllm==0.9.0 --hash=sha256:aa --bogus`,
        `vllm==0.9.0 --hash=sha256:aa -Cbad`, `vllm==0.9.0 --hash=md5:aa`,
        `vllm==0.9.0 --hash=sha256`;
      - accepted, assert `_parse_pins(text) == {"vllm": "0.9.0"}` and
        `_unpinned_protected(text) == {}`: `vllm==0.9.0 -Cfoo"="bar`,
        `vllm==0.9.0 --hash sha256:aa`, `vllm==0.9.0 --hash=sha384:aa -Cfoo=bar`.
      Record the pip 26.1.2 verdict of each row in the docstring (design D1 has them). Confirm
      the five rejected rows and the `-Cfoo"="bar` row FAIL on their assertion, not on a
      collection error. `--hash sha256:aa` and `--hash=sha384:aa -Cfoo=bar` already pass at
      `5564e016` — they are regression controls, and that is expected. Do not change them to
      make them red.

      **Then green.** Add a helper `_break_args_options(line)` that copies pip's split (design
      D1, verbatim logic) and a helper that reads the option half with `shlex.split` against
      the closed table of design D1. In `_requirement_lines`: after the comment cut, apply the
      split; if the option half is non-empty and accepted, keep only the requirement half; if
      rejected, keep the whole line. Stop cutting at `;` in `_requirement_lines` (design D2).
      Every reader that still matches `_PIN_PATTERN` or `_REQUIREMENT_PATTERN` cuts the marker
      itself with `line.split(";", 1)[0].strip()` first. Make `_conditional_protected` iterate
      `_requirement_lines(text)` and partition on `;` itself. Delete `_PER_REQUIREMENT_OPTION`
      and `_CONFIG_SETTING_VALUE` once nothing reads them. Move the measured pip facts from
      their comments into the new helpers' docstrings, shorter.

      All **254** existing tests must stay green (design D4 measured this state).
      `test_every_config_settings_value_pip_accepts_is_still_cut` is the anti-over-narrowing
      guard; it must stay green.

      Then: format, `git add`, `bash scripts/gate.sh` exits 0, commit
      `fix(#530): read the option half of a requirement line as pip does`.

## 2. Commit B — the requirement half (design D2, D3, D5, D6)

- [x] 2.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add a test class `TestRequirementHalfReadsAsPipDoes` after the class from
      task 1. Parametrize, each row with its pip 26.1.2 verdict in the docstring (design D7):
      - reported (`_opaque_requirements(text + "\n")` is non-empty): the four bare-operator
        rows; the 12 #528 rows (attached `example.zip<X>==1.0`, detached
        `example.zip <X> ==1.0`, named URL `numpy <X> @ https://host/numpy.whl`, for X in
        `[foo bar]`, `[foo,]`, `[,foo]`, `[-foo]`); the four non-ASCII rows
        `example.zip [İ] ==1.0`, `[ı]`, `[ſ]`, `[K]` — write these with
        `\u` escapes in the Python source, never as literal characters; and the three rejected
        option rows from task 1 (commit B makes them reported, design D4);
      - not reported (`_opaque_requirements(text + "\n") == []`): `example.zip [foo] ==1.0`
        and the 18 accept controls (the same three spellings for `[foo]`, `[foo,bar]`,
        `[ foo , bar ]`, `[]`, `[ ]`, `[foo.bar_1]`);
      - pins (design D3): `vllm == 0.9.0`, `vllm (==0.9.0)`, `vllm===0.9.0` give
        `{"vllm": "0.9.0"}`; `vllm==1.0.*` and `vllm==0.9.0,<0.10` give `{}` and
        `"vllm" in _unpinned_protected(text)`;
      - stricter than pip (design D6), each reported, each with a `# stricter than pip:` comment
        that names the reason: `vllm-0.9.0-py3-none-any.whl[foo]`, `evil@../pkgs/vllm`,
        `vllm==0.9.0 --no-binary :all:`, `vllm==0.9.0 --has=sha256:aa`.
      - markers (design D2 steps 5–6, D7 second table): `vllm==0.9.0; os_name == "a -Cbad"`,
        `vllm==0.9.0 ; os_name == "x --bogus"` and `vllm==0.9.0 ; bogus marker` give
        `_parse_pins(text) == {}`, are reported, and `"vllm" in _unpinned_protected(text)`;
        `vllm==0.9.0 ;` gives `{"vllm": "0.9.0"}`, is not reported, and
        `_conditional_protected(text) == {}`; `sympy==1.13.1;` gives `{"sympy": "1.13.1"}`.
      Confirm that the rows that read a pin or miss a report at `5564e016` FAIL on their
      assertion. Many rows already pass at `5564e016` and are regression controls — that is
      expected, and the list here is not complete: the accept controls, the two D4 rows, the
      four detached #528 rows (`example.zip <X> ==1.0`), `vllm==0.9.0,<0.10`, and the two
      empty-marker rows all pass today. Never change a control to make it red. The fact that
      matters is that every row passes after the green step.

      **Then green.** Add the single reader of design D2 returning
      `(name, requirement_or_None, reported)`, with `packaging.requirements.Requirement` and
      `InvalidRequirement` imported next to the existing `packaging` imports. Route
      `_parse_pins`, `_opaque_requirements`, `_parse_requirements`, `_declarations` and
      `_conditional_protected` through it, with the pin, specifier-text and marker rules of
      design D3 (source specifier text, not `str(req.specifier)`). Delete the constants listed
      in design D5. Update the `_opaque_requirements` docstring to describe the classifier
      order against pip's `_get_url_from_path` / `install_req_from_line`.

      Then confirm: `grep -nE '^(_PIN_PATTERN|_REQUIREMENT_PATTERN|_COMPARISON_AFTER_SUFFIX|_ATTACHED_EXTRAS|_EXTRA_NAME|_VALID_EXTRAS_LIST|_NAME_WITH_SPACED_EXTRAS|_NAMED_URL_REFERENCE|_PATH_OR_ARCHIVE_REQUIREMENT) ' tests/unit/test_base_image_dependency_compatibility.py`
      prints nothing. Docstrings of existing tests may still name the old constants as
      history — leave them.

      Then: format, `git add`, gate green, commit
      `fix(#530): read the requirement half with packaging, in pip's order`.

## 3. Prove the whole thing, then publish

- [x] 3.1 Run the acceptance checks and confirm every number. All of:
      the monitored-files command prints `[] 1 {} {}`, `[] 5 {} {}`, `[] 105 {} {}`,
      `[] 106 {} {}`, `[] 110 {} {}`;
      `python -m pytest tests/unit/test_base_image_dependency_compatibility.py -q` reports
      **more than 254 passed** and **0 failed**;
      the deleted-test count prints `0`;
      `grep -n 'pip._internal' tests/unit/test_base_image_dependency_compatibility.py` shows
      only comments or docstrings, never an `import`;
      `bash scripts/gate.sh` exits 0;
      `git status --porcelain` is empty.
      Commit only if something changed; otherwise this task commits nothing and you move on.

- [x] 3.2 Push the branch and open the PR. The branch was cut with `checkout -b`, so its
      upstream is `origin/dev` — push with
      `git push -u origin fix/issue-530-pip-requirement-reader` to repoint it.
      Confirm the push landed on **fasrc/archi**, not a fork:
      `git ls-remote --heads origin fix/issue-530-pip-requirement-reader` must print the same
      SHA as `git rev-parse HEAD`.
      Write the PR body to `/tmp/pr-body-530.md` — **never** under `docs/` — and include the
      literal line `Closes #530`. List the two corrections to the issue body (design D1) and
      the four stricter-than-pip rows (design D6). Then
      `gh pr create --repo fasrc/archi --base dev --title "fix(#530): read requirement lines as pip does in the base-image dependency guard" --body-file /tmp/pr-body-530.md`.
      Verify the link: `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences`
      must list 530. If it does not, edit the body and re-verify.

- [x] 3.3 Reply in the five threads on PR #527 — 4069851117, 4069851128, 4069452153,
      4080523425 and 4080582910 — each with the SHA of the commit that closes it (task 1 for
      option findings, task 2 for requirement findings). Use the review-comment reply API
      (`gh api repos/fasrc/archi/pulls/527/comments/<id>/replies -f body=...`), not a new
      top-level comment. Do not use `gh pr comment --json`; that flag is unsupported. If a
      reply fails with 403 or 404, record the failure in the PR body and move on.
      Then STOP. Do not merge this PR. A human merges, in daylight.

## Commands

```bash
# the module's own suite — 254 passed at 5564e016
python -m pytest tests/unit/test_base_image_dependency_compatibility.py -q --no-header

# the monitored files — after every task
python3 - <<'PY'
import importlib.util
from pathlib import Path
s = importlib.util.spec_from_file_location("g", "tests/unit/test_base_image_dependency_compatibility.py")
g = importlib.util.module_from_spec(s); s.loader.exec_module(g)
for f in [g.CPU_HEADER, g.GPU_HEADER, g.BASE_REQUIREMENTS,
          g._DOCKERFILE_TEMPLATES / "base-python-image" / "requirements.txt",
          g._DOCKERFILE_TEMPLATES / "base-pytorch-image" / "requirements.txt"]:
    t = Path(f).read_text()
    print(g._opaque_requirements(t), len(g._parse_pins(t)), g._unpinned_protected(t), g._conditional_protected(t))
PY

# no test was deleted — must print 0
git diff origin/dev -- tests/unit/test_base_image_dependency_compatibility.py | grep -c '^-.*def test_'

# the gate, before every commit
bash scripts/gate.sh
```
