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
  command in task 4.1.

## 1. The release call matches every current reference

- [ ] 1.1 `model: opus` — RED test in `tests/unit/test_update_service_base_images.py`. Add a
      helper that reads the argv out of a named step of a workflow file: parse the YAML, find
      the step by its `name`, take its `run` string, replace every `${{ … }}` expression with a
      supplied value, and `shlex.split` the result. The test must drive the script with the
      argv it read from the **release** workflow's "Point Dockerfiles to versioned base images"
      step against a `dev-4314ac4`-pinned fixture, and assert the line becomes the release tag.
      The test must read the argv from the file rather than restate it, so that removing
      `--orig-tag all` from the workflow turns the test red. Watch it fail. Then add
      `--orig-tag all` to `.github/workflows/test-and-build-tag.yml:154`. Gate green; commit.
- [ ] 1.2 `model: sonnet` — Correct the docstring of
      `test_the_default_orig_tag_only_reaches_a_latest_pinned_line`. It states that the release
      workflow passes no `--orig-tag` and that issue #339 tracks the fix. Both halves are now
      wrong. The test itself does not change: it pins the **script's default**, which this
      change leaves alone. Gate green; commit.

## 2. The release run proves the retarget happened

- [ ] 2.1 `model: opus` — RED tests for a new `--verify` mode on
      `scripts/dev/update_service_base_images.py`. Four cases: a tree whose base lines all
      carry the target reference exits 0 and writes nothing; a tree with one line on another
      tag exits non-zero and names that file; a tree with no `a2rchi-*-base` line at all exits
      non-zero; and `--verify` with no `--tag` exits non-zero. Verification reuses the
      rewriter's own line parser, so the check cannot drift from what the rewriter treats as a
      base line. Watch them fail. Implement. Gate green; commit.
- [ ] 2.2 `model: sonnet` — RED test that the release workflow carries the verification step,
      that it runs after "Point Dockerfiles to versioned base images" and before "Run smoke
      deployment", and that it verifies the same tag and source the retarget step writes. Watch
      it fail. Then add the step to `.github/workflows/test-and-build-tag.yml`. Gate green;
      commit.
- [ ] 2.3 `model: haiku` — Replace the commit step's `No Dockerfile updates to commit` message
      with one that states the templates already carry the release tag, and that the
      verification step above proved it. The empty-diff branch stays non-fatal; the proposal
      records why. No test. Gate green; commit.

## 3. Close-out

- [ ] 3.1 `model: sonnet` — Run the whole file: `python -m pytest
      tests/unit/test_update_service_base_images.py -v`. Run the YAML parse check from the
      issue on both workflow files. Re-run the reproduction from the issue against a copy of
      the real templates with the workflow's new argv and record the rewritten count.
- [ ] 3.2 `model: haiku` — Run `bash scripts/gate.sh` in the container once more on the
      finished branch and confirm it exits 0. Confirm `git status` is clean. Push with
      `git push -u origin fix/issue-339-release-retarget-orig-tag`. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, `closes #339` in the **body**, and the
      body's Findings block carrying the pre-PR review's surviving findings. Do not merge.
