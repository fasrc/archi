## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-556-answer-path-config-gate` exists, cut from `origin/dev` at
      `0ddc96e1`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-556-answer-path-config-gate --strict` passed on the
      host. The `openspec` CLI is **not usable in this container** — do not run it, and do not
      add a task that does. It is already green.
- [x] 0.3 The red is measured at `0ddc96e1`. Two single-arm artifacts that differ only in
      `services.chat_app.context_editing` (one set, one absent) → `cr.main` returns **0**. One
      arm with no `configuration` key at all → **0**. Both must return **2** (`EXIT_GATE`).
      Module baseline: **99 passed in 0.67s**. The probe is in `## Commands` below.
- [x] 0.4 Every design decision is made in `design.md` (D1–D10): the two refused paths, absent
      ≠ null, the override flag's contract, the unknown-path usage error, the reported path,
      the not-recorded rule, replicates unchanged, gate id `G10`. You are not searching for an
      approach. Follow the design; where the issue body and the design differ, the design wins.

## Rules that apply to EVERY task below

- All code work is in `scripts/benchmarking/compare_runs.py` and
  `tests/unit/test_compare_runs.py`; task 4 also edits
  `docs/docs/interpreting_benchmark_results.md`. **No `src/` file changes in this change.**
  Do not touch `corpus_gate`, `divergence_gate`, `check_noise_replicates`, or `load_arms`.
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed and the loop halts there.
- **Do not implement a later task's behaviour early.** Each task's red must still fail when
  that task starts (design D10). Task 1 adds no flag; task 2 adds no `not recorded` wording
  and no reported path.
- Before you add a test, `grep -n 'def test_' tests/unit/test_compare_runs.py` and pick a
  name that is not already used. The gate runs no linter, so a duplicate `def test_...`
  silently replaces the older test while the suite stays green.
- Put the new tests in a new section. Insert the marker line
  `# --- G10: the answer-path gate ---------------------------------------------` directly
  **above** the existing `# --- G8: the anchors` marker, and add every new test between the
  two markers. Never append at the end of the file.
- After you insert a test, `git diff` the file and **read the trailing context line**.
  Inserting inside an existing test body instead of after it silently steals that test's
  last assertion. Confirm the test above yours still ends with its own `assert`.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/unit/test_compare_runs.py | grep -c '^-.*def test_'` must
  print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. The pre-commit
  hook's black is a writer while CI's is an assert, so a commit can be pushed misformatted.
  After each commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer.
- Do not write any file under `docs/` except the one edit in task 4. The PR body goes in
  `/tmp`, never in the repo. Do not add an entry to `docs/questions.md`.
- Render every recorded value the same way in the refusal message and in the gate row:
  `absent` for a missing path, `null` for a recorded null, `not recorded` for an arm without a
  configuration mapping, and `json.dumps(value, sort_keys=True)` for everything else
  (design D2). Tests assert on these spellings.
- `scripts/` is not measured by the coverage gate (`--cov=src`), so `diff-cover` reports no
  coverable lines for this diff and the 80% bar is met vacuously. The tests are the only bar
  the tool has to clear. Write each one to pin a behaviour a wrong answer would silently pass.

## 1. The gate: refuse a differing answer path, pass an identical one

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Extend the `_artifact` fixture's `make(...)` with a keyword
      `configuration=None`. `None` (the default) keeps today's `"configuration": {}` for every
      arm, so the 99 existing tests are untouched. A dict applies to every arm; a per-arm
      list applies one entry per arm (use the existing `_per_arm`). The string `"absent"`
      omits the `configuration` key for that arm, the way `corpus_unchanged="absent"` already
      omits its key. Run the suite and confirm **99 passed** still.

      Then add, in the new `G10` section, tests that assert:
      (a) two single-arm artifacts with identical `fingerprint` whose configurations are
      `{"services": {"chat_app": {"recursion_limit": 50, "context_editing": {"trigger": 32768, "keep": 1}}}}`
      and `{"services": {"chat_app": {"recursion_limit": 50}}}` → `cr.main([base, treat]) ==
      cr.EXIT_GATE`, and `capsys.readouterr().err` contains `G10`,
      `services.chat_app.context_editing`, `32768` and `absent`;
      (b) the same pair with `"context_editing": None` in the second arm → `EXIT_GATE`, and the
      stderr contains `null` and `32768` (absent and null are both differences from the bound;
      design D1);
      (c) two arms with byte-identical answer-path values (both carry the bound and
      `recursion_limit: 50`) → `cr.EXIT_OK`; the stdout has a line starting `| G10 one answer path | pass |`
      (read it the way the G8 tests read their row, `line.startswith("| G10 ")`); and with
      `--json <tmp_path>/out.json` the written report's `gates` list holds exactly one entry
      with `id == "G10"` whose `status == "pass"` and whose `detail` names both refused paths;
      (d) `cr.ANSWER_PATH_REFUSED == ("services.chat_app.context_editing", "services.chat_app.recursion_limit")`
      and `cr.ANSWER_PATH_REPORTED == ("services.benchmarking.agent_md_file",)`, so a future
      list edit cannot desynchronise tests from tool.
      Run `python -m pytest tests/unit/test_compare_runs.py -q --no-header` and confirm (a),
      (b), (c) and (d) FAIL on their assertion or on the missing attribute — not on a fixture
      error.

      **Then green.** In `compare_runs.py`, beside `corpus_gate`: the two constants
      `ANSWER_PATH_REFUSED` and `ANSWER_PATH_REPORTED` (design D9); a module-level sentinel for
      absent; `recorded_setting(configuration, path)` that walks `path.split(".")` through
      mappings and returns the sentinel for a missing step or a non-mapping intermediate;
      `_show_setting(value)` that renders as the rules above say; and
      `answer_path_gate(arms)` that reads each refused path from
      `arm.raw.get("configuration")` for every arm, compares the rendered values, and raises
      `CompareError(..., EXIT_GATE)` naming **every** differing path with each arm's
      `label=value`, one sentence on why (the bound and the limit decide which questions the
      agent can finish, so the delta would measure the configuration), and the way past it
      (re-run with one answer-path configuration, or `--config-differs-by-design <path>`) —
      design D3. When nothing differs return
      `{"id": "G10", "name": "one answer path", "status": "pass", "detail": ...}` with the
      detail listing each refused path's per-arm values. Do **not** add the flag in this task;
      the gate takes no override argument yet. Treat an arm whose `configuration` is not a
      mapping as every path absent for now — task 3 changes that.
      Wire it into the gate list in `run` directly after `divergence_gate(...)`.

      Then: format, `git add`, `bash scripts/gate.sh` exits 0, commit
      `fix(#556): refuse arms whose answer-path configuration differs (G10)`.

## 2. The override: waive one named path, print both values, reject an unknown path

- [x] 2.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add tests asserting:
      (a) the task-1 pair (bound vs absent) with
      `--config-differs-by-design services.chat_app.context_editing` → `cr.EXIT_OK`; the
      stdout `| G10 ` row's status starts with `OVERRIDDEN` and names
      `--config-differs-by-design services.chat_app.context_editing`; the row (or the JSON
      `detail`) contains both `32768` and `absent`;
      (b) two arms that differ only in `services.chat_app.recursion_limit` (`50` vs `25`) →
      `EXIT_GATE`, stderr names `services.chat_app.recursion_limit`, `50` and `25`; the same
      pair with `--config-differs-by-design services.chat_app.context_editing` **still**
      returns `EXIT_GATE` (a different path does not waive it); with
      `--config-differs-by-design services.chat_app.recursion_limit` it returns `EXIT_OK`;
      (c) both refused paths differ and only one is named → `EXIT_GATE`, and the stderr names
      the un-waived path;
      (d) `--config-differs-by-design services.chat_app.default_model` → `cr.EXIT_USAGE`, and
      the stderr lists both accepted paths; `--config-differs-by-design services.benchmarking.agent_md_file`
      → `EXIT_USAGE` too (reported, never refused, so nothing to waive — design D4);
      (e) naming a path that does **not** differ leaves the row at `pass` (design D4).
      Confirm (a)–(e) fail — today the parser rejects the unknown flag, so they fail with
      `SystemExit` from argparse; assert the exit path you expect once the flag exists, and
      confirm the failure is not a fixture error.

      **Then green.** Add `--config-differs-by-design` directly after
      `--corpus-differs-by-design` in the parser: `action="append"`, `default=[]`,
      `metavar="DOTTED.PATH"`, help naming the two accepted paths and that both values are
      still printed. Give `answer_path_gate` an `allow_differs: Sequence[str]` parameter and
      pass `args.config_differs_by_design`. Inside the gate: any name not in
      `ANSWER_PATH_REFUSED` → `CompareError(..., EXIT_USAGE)` listing the accepted paths; a
      differing path that is named is collected instead of refused; if every differing path
      is named, return the row with
      `status = "OVERRIDDEN (--config-differs-by-design <p1>[, <p2>])"` listing only the
      paths that were both named and differed, and a detail printing each arm's value for
      every refused path. Never omit a value.

      Then: format, `git add`, gate green, commit
      `fix(#556): add --config-differs-by-design to waive one named answer-path setting`.

## 3. The reported path, and an arm with no recorded configuration

- [x] 3.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add tests asserting:
      (a) two arms whose refused paths are identical and whose
      `services.benchmarking.agent_md_file` differs (`"prompts/a.md"` vs `"prompts/b.md"`) →
      `EXIT_OK`, `| G10 ` row status `pass`, and the row (or JSON `detail`) contains
      `agent_md_file`, `prompts/a.md` and `prompts/b.md`; and a pair where it is equal has no
      `agent_md_file` in the G10 detail (design D5);
      (b) two arms built with `configuration="absent"` (no key at all) → `EXIT_GATE`; the
      stderr contains `G10` and `not recorded` (today this pair passes, because absent equals
      absent — that is this task's red);
      (c) one recorded arm and one `configuration="absent"` arm → `EXIT_GATE` and the stderr
      says `not recorded` next to the unrecorded arm's label;
      (d) the pair in (b) with **both** `--config-differs-by-design services.chat_app.context_editing`
      and `--config-differs-by-design services.chat_app.recursion_limit` → `EXIT_OK`, row
      status starts with `OVERRIDDEN`, detail contains `not recorded`; with only one of the two
      named → still `EXIT_GATE` (design D6);
      (e) a `configuration` recorded as `null` (per-arm list `[cfg, None]` — extend the
      fixture so an explicit `None` entry in a per-arm list writes `"configuration": null`)
      counts as not recorded, the same as a missing key.
      Confirm (a)–(e) fail on their assertions.

      **Then green.** In `answer_path_gate`: an arm whose `arm.raw.get("configuration")` is
      not a mapping is **not recorded** — collect its label, render `not recorded` for every
      path, and refuse unless every path in `ANSWER_PATH_REFUSED` is in `allow_differs`
      (design D6). Read each `ANSWER_PATH_REPORTED` path with the same reader; when its
      rendered values differ across arms, append
      `services.benchmarking.agent_md_file differs (reported, not refused): <label>=<value>, ...`
      to the detail, and never let it change the status (design D5).

      Then: format, `git add`, gate green, commit
      `fix(#556): report agent_md_file and refuse an arm with no recorded configuration`.

## 4. Name the gate where the gates are listed, then prove the whole thing

- [x] 4.1 Edit the module docstring of `compare_runs.py`: add one bullet after the
      **Procedure E** bullet, in the same voice as the others, that names **G10 — one answer
      path**, the two refused paths, `--config-differs-by-design`, and that
      `agent_md_file` is reported rather than refused. Leave the `Exit codes` line as it is.
      Edit `docs/docs/interpreting_benchmark_results.md` in exactly four places (re-`grep`,
      the line numbers have moved): the Procedure C sentence `It implements G3–G8 in one
      tested place` → `G3–G10`; a new flag-table row directly after the
      `--corpus-differs-by-design` row for `--config-differs-by-design DOTTED.PATH` (the
      only way past the G10 answer-path gate for one named setting; names the two accepted
      paths; repeatable; prints both values and marks the row OVERRIDDEN; never hides the
      difference); a new bullet in **What it refuses** directly after the G3 bullet (the
      arms recorded different answer-path settings, or an arm recorded no `configuration`
      at all — G10; why: the bound and the limit decide which questions the agent can
      finish, with the 2026-09-19 case, 6 of 109 lost, as the example; the override; and
      that `agent_md_file` is reported, never refused, because prompt arms vary it on
      purpose); and one clause in the Gap 1 paragraph's refusal list (`or when the arms
      recorded different answer-path settings (G10)`). Add **no** heading and **no** anchor.
      Then run `python -m pytest tests/unit/test_compare_runs.py -q --no-header` and confirm
      at least **110 passed, 0 failed**; the deleted-test count prints `0`; the probe in
      `## Commands` now prints `2` for both cases; `bash scripts/gate.sh` exits 0;
      `git status --porcelain` is empty after the commit. Commit
      `docs(#556): describe the G10 answer-path gate and its override flag`.

## 5. Publish

- [x] 5.1 Push the branch and open the PR. The branch was cut with `checkout -b`, so its
      upstream is `origin/dev` — push with
      `git push -u origin fix/issue-556-answer-path-config-gate` to repoint it. Confirm the
      push landed on **fasrc/archi**, not a fork:
      `git ls-remote --heads origin fix/issue-556-answer-path-config-gate` must print the same
      SHA as `git rev-parse HEAD`. If it prints nothing, the push went elsewhere — stop and
      write the halt reason to `STATUS.md`.
      Write the PR body to `/tmp/pr-body-556.md` — **never** under `docs/` — and include the
      literal line `Closes #556`, the measured red (both probes `0` at `0ddc96e1`), the test
      count before and after, and the G10 contract in five lines. If no PR exists for the
      branch yet (`gh pr list --repo fasrc/archi --head fix/issue-556-answer-path-config-gate`
      is empty), run
      `gh pr create --repo fasrc/archi --base dev --title "fix(#556): refuse arms whose answer-path configuration differs (G10)" --body-file /tmp/pr-body-556.md`.
      If the review gate already opened one, `gh pr edit <pr> --repo fasrc/archi --body-file /tmp/pr-body-556.md`
      instead. A `Closes #556` in the title does not link the issue; it must be in the body.
      Verify the link: `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences`
      must list 556. If it does not, edit the body and re-verify.
      Then STOP. Do not merge this PR. A human merges, in daylight.

## Commands

```bash
# the module's own suite — 99 passed at 0ddc96e1, at least 110 when done
python -m pytest tests/unit/test_compare_runs.py -q --no-header

# the red: both lines print 0 at 0ddc96e1 and must print 2 when done
python3 - <<'EOF'
import json, sys, tempfile, pathlib, io, contextlib
sys.path.insert(0, ".")
from scripts.benchmarking import compare_runs as cr
tmp = pathlib.Path(tempfile.mkdtemp())
def arm(cfg, omit=False):
    e = {"single_question_results": {"question_1": {"question": "q1", "reference_answer": "r", "answer": "a", "status": "ok", "messages": [], "reference_sources_match_fields": ["url"], "reference_sources_metadata": [], "sources_metadata": [], "sources_trunc_content": [], "time_elapsed": 1.0, "faithfulness": 0.5}},
         "total_results": {"aggregate_faithfulness": 0.5, "faithfulness_scored": "1 of 1", "source_scored_count": 0, "source_accuracy": None, "relative_source_accuracy": None},
         "configuration_file": "configs/a.yaml",
         "config_version": {"digest": "sha256:a", "source": "t", "selected_file": "configs/a.yaml", "selected_file_digest": "sha256:a", "divergence_from_selected_file": None, "key_settings": {}},
         "corpus_fingerprint": "corpus-1"}
    if not omit: e["configuration"] = cfg
    return e
def write(name, entry):
    p = tmp / name; p.write_text(json.dumps({"benchmarking_results": [entry], "metadata": {"time": "t", "git_info": {"last_commit": "x"}, "corpus_snapshot_id": "s1", "code_version": {"digest": "c1"}, "config_versions": ["sha256:a"]}})); return str(p)
base = write("base.json", arm({"services": {"chat_app": {"recursion_limit": 50, "context_editing": {"trigger": 32768, "keep": 1}}}}))
treat = write("treat.json", arm({"services": {"chat_app": {"recursion_limit": 50}}}))
noconf = write("noconf.json", arm(None, omit=True))
for label, argv in (("context_editing differs", [base, treat]), ("no configuration", [base, noconf])):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = cr.main(argv)
    print(label, code)
EOF

# no test was deleted — must print 0
git diff origin/dev -- tests/unit/test_compare_runs.py | grep -c '^-.*def test_'

# the gate, before every commit
bash scripts/gate.sh
```
