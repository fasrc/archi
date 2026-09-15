# Tasks — make the release retarget work and prove it

Every checkbox below is one loop turn and ends **green and committed**. Write the failing test,
watch it fail for the right reason, write the smallest fix, run `bash scripts/gate.sh`, commit.
Never end a task with the suite red, and never use `--no-verify`.

Three standing notes for every task:

- Coverage: `scripts/gate.sh:146` runs `--cov=src`, so nothing in `scripts/` and nothing in
  `.github/` reports to `diff-cover`. Do not read a green patch-coverage line as evidence. The
  named tests are the evidence. Black and isort **do** enforce `scripts/` and `tests/`.
- Scope: do not edit `.github/workflows/pr-preview.yml`, the Dockerfile templates,
  `deploy/**`, or `src/**`. Do not change the `--orig-tag` default.
- The gate runs in the `localhost/archi-loop:latest` container, not in a local shell. See the
  command in task 3.2.

## 1. The release call matches every current reference

- [x] 1.1 `model: opus` — RED test in `tests/unit/test_update_service_base_images.py`. Add a
      helper that reads the argv out of a named step of a workflow file: parse the YAML, find
      the step by its `name`, take its `run` string, replace every `${{ … }}` expression with a
      supplied value, and `shlex.split` the result. The test must drive the script with the
      argv it read from the **release** workflow's "Point Dockerfiles to versioned base images"
      step against a `dev-4314ac4`-pinned fixture, and assert the line becomes the release tag.
      The test must read the argv from the file rather than restate it, so that removing
      `--orig-tag all` from the workflow turns the test red. Watch it fail. Then add
      `--orig-tag all` to `.github/workflows/test-and-build-tag.yml:154`. Gate green; commit.
- [x] 1.2 `model: sonnet` — Correct the docstring of
      `test_the_default_orig_tag_only_reaches_a_latest_pinned_line`. It states that the release
      workflow passes no `--orig-tag` and that issue #339 tracks the fix. Both halves are now
      wrong. The test itself does not change: it pins the **script's default**, which this
      change leaves alone. Gate green; commit.

## 2. The release run proves the retarget happened

- [x] 2.1 `model: opus` — RED tests for a new `--verify` mode on
      `scripts/dev/update_service_base_images.py`. Four cases: a tree whose base lines all
      carry the target reference exits 0 and writes nothing; a tree with one line on another
      tag exits non-zero and names that file; a tree with no `a2rchi-*-base` line at all exits
      non-zero; and `--verify` with no `--tag` exits non-zero. Verification reuses the
      rewriter's own line parser, so the check cannot drift from what the rewriter treats as a
      base line. Watch them fail. Implement. Gate green; commit.
- [x] 2.2 `model: sonnet` — RED test that the release workflow carries the verification step,
      that it runs after "Point Dockerfiles to versioned base images" and before "Run smoke
      deployment", and that it verifies the same tag and source the retarget step writes. Watch
      it fail. Then add the step to `.github/workflows/test-and-build-tag.yml`. Gate green;
      commit.
- [x] 2.3 `model: haiku` — Replace the commit step's `No Dockerfile updates to commit` message
      with one that states the templates already carry the release tag, and that the
      verification step above proved it. The empty-diff branch stays non-fatal; the proposal
      records why. No test. Gate green; commit.

## 3. Close-out

- [x] 3.1 `model: sonnet` — Run the whole file: `python -m pytest
      tests/unit/test_update_service_base_images.py -v`. Run the YAML parse check from the
      issue on both workflow files. Re-run the reproduction from the issue against a copy of
      the real templates with the workflow's new argv and record the rewritten count.

      **Measured**, driving both steps' argv read from the workflow against a copy of the real
      `src/cli/templates/dockerfiles/`: verify before the retarget exits 1 and names all 15
      templates; the retarget rewrites **15 of 15** (was 0 of 15); verify after exits 0 with
      `Verified 15 base references at v2026.8.0.`; a second retarget of the same tag writes
      nothing and verify still exits 0. Both workflow files parse.
- [x] 3.2 `model: haiku` — Run `bash scripts/gate.sh` in the container once more on the
      finished branch and confirm it exits 0. Confirm `git status` is clean. Push with
      `git push -u origin fix/issue-339-release-retarget-orig-tag`. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, `closes #339` in the **body**, and the
      body's Findings block carrying the pre-PR review's surviving findings. Do not merge.

      Opened as PR #362.

## 4. Pre-PR adversarial review (three rounds)

Recorded here because the loop ran before any PR existed to comment on. The surviving findings
are the PR body's Findings block.

- [x] 4.1 Round 1 — `[high]`: `--verify` counted matching lines in aggregate, so a template
      moved onto a **renamed** `a2rchi` base was skipped by the rewriter and by the check
      alike, and one other correct template carried the run green. **Held.** The check now
      refuses any `a2rchi` base it cannot place, and the spec states the bound: a template
      declaring no base image at all stays invisible, as it is to `required_base_images` and to
      `test_service_templates_pin_one_explicit_base_tag`. Residual filed as **#361**.
- [x] 4.2 Round 2 — `[high]`: the release tag is cut from a tree the smoke test never ran.
      **Held in part.** Its premise that this change removes a push guard is inverted — the
      retarget was a total no-op, so that push never ran at all, and this change restores it.
      The drift itself is pre-existing and untouched here (the diff changes no `ref:`), and
      closing it means resolving one commit per job, which trades against the release mechanics
      that push to the dispatched ref by name. Took the half that fits: a verification step in
      the `release` job, immediately before the tag is created. Remainder filed as **#360**.
- [x] 4.3 Round 3 — no new in-scope finding. Restated round 2's drift, already conceded in the
      spec and the workflow comment; and flagged `context_windows` in
      `src/archi/pipelines/agents/utils/context_middleware.py`, which is not in this diff and
      is already documented in its own docstring as a known limitation tracked by **#344**.
      Terminal condition: only deferred findings remain.

## 5. Post-PR review (PR #362)

- [x] 5.1 Post-PR round 1 on #362 — Greptile 5/5, no blocking issue. Codex raised three, all
      verified against the code before acting. `[P1]` the release job's verification ran after
      `Promote base images to latest` and `Commit version bump`, so a failure left the `latest`
      tags moved and the bump pushed — **held**, fixed by checking the fresh checkout first and
      keeping the late check for the tree those steps leave behind. `[P1]` `--verify` was
      undocumented while `docs/docs/developer_guide.md:479` documents every other flag and
      `AGENTS.md:53` requires it — **held**, documented. `[P2]` the task checkboxes were
      unticked — **already fixed** in `ae72349d`, which was held back unpushed so it could ride
      with these fixes.
- [x] 5.2 Post-PR round 2 on `f6bad9ea` — two findings, both **held**. `[P2]` the new developer-guide
      example passed `--tag v2026.8.0`, but this repository's release tags are zero-padded
      (`v2026.08.0`, confirmed against the four open milestones) and `--verify` compares the tag
      exactly, so an operator copying that line would have seen every reference reported wrong.
      Fixed, with the contrast spelled out in the example. `[P3]` the proposal's Impact section
      still read "`scripts/dev/update_service_base_images.py` — **not** edited", which its own
      "What Changes" section had already contradicted, and still counted one verification step
      and one test. Rewritten to the delivered scope. A local adversarial pass over the same tip
      returned only the #360 drift, which its own recommendation calls a known remaining hole.
