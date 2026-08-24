# Tasks - fix-issue-326-crash-safe-overwrite-manifest

> Every numbered group below ends with the suite green and the project gate passing. A red
> test and the code that turns it green belong to the SAME group and the SAME commit,
> because a task that ends red can never be committed and the loop cannot proceed past it.
> Watch each red test fail mid-group and keep the output for the PR body.
>
> "The project gate" means the single documented gate command for this repo. Run it
> unmodified before every commit and never bypass it:
> `PATH=/home/austin/miniforge3/envs/archi/bin:$PATH bash scripts/gate.sh`
>
> Line anchors are as of `origin/dev` at `3de206bc`. Re-derive before citing any of them in
> the PR body - do not paste these numbers forward.
>
> `src/evaluation/qa/workflow.py`, `tests/unit/evaluation/qa/test_workflow.py` and
> `src/evaluation/qa/artifacts.py` are all black-clean under black 24.10.0 (verified on this
> branch's base), so an in-place edit reflows no unrelated line and patch coverage is measured
> against the lines you actually wrote.
>
> Scope fences (design.md D5) - if you find yourself editing any of these, stop and reduce the
> change: `src/evaluation/qa/schema.py` (the issue forbids it), the terminal status
> assignments at `:500`, `:597`, `:862` and the publishes at `:502`, `:506`, `:600`, `:881`
> (already correct), `_remove_owned` (`:84-97`) and `OWNED_FILES` (design.md D2 exists to avoid
> them), `history.py` / `console.py` (readers, not writers), and `prepare()` (`:192-217`).

## 1. The `run(overwrite=True)` publish is a valid document (red test + fix, one commit)

- [ ] 1.1 Confirm the defect first, so the red you see next is the right red. From the repo
      root, build a `qa-v2` manifest with `prepare`/`run`/`score` phases `completed` and the
      full digest map, apply the `:352-364` prune verbatim, and call
      `RunManifest.from_dict` for each prior status in
      `("scored", "run_completed", "attention_required")`. Expected: `scored` and
      `run_completed` raise `manifest phase state is incomplete`; `attention_required` raises
      `manifest attempts must be a positive integer`. Then set `status` to `"prepared"` on the
      same three and confirm all are accepted. Keep the before/after table for the PR body -
      it is the whole evidence for this change.
- [ ] 1.2 Add a recorder helper to `tests/unit/evaluation/qa/test_workflow.py` that
      monkeypatches `write_json` in the `src.evaluation.qa.workflow` module namespace, and for
      every call whose path ends in `manifest.json` stores `copy.deepcopy(payload)` before
      delegating to the real function. The deep copy is not optional (design.md D4): the
      workflow mutates one dict for the length of the call, so a stored reference reads back
      at its final value and the test passes against the unfixed code. Prove the helper works
      by asserting it captured more than one document.
- [ ] 1.3 Write the RED test: drive `run(overwrite=True, authorize_staged_invalid=True)` over
      the existing paused-run fixtures in this file (`test_live_workflow.py` and the fakes
      already in `test_workflow.py` are the models to follow - reuse them, do not build a new
      workspace harness), then assert every captured `manifest.json` payload round-trips
      through `RunManifest.from_dict`. Run it and watch it fail on the document published at
      `:380`. Paste the failure into the PR body.
- [ ] 1.4 Make it green with the smallest change: in the `if overwrite:` block of `run()`
      (`:352-364`), set `manifest["status"] = "prepared"` after the pops. Do not touch the
      pops themselves, the `runtime_phase` assignment at `:375-379`, or the publish at `:380`.
      design.md D1 has the argument for `prepared` specifically - `attempts` is popped, and it
      is the only status that tolerates its absence.
- [ ] 1.5 Assert the demotion is invisible downstream: the manifest the call **returns** still
      reports the real terminal status (`run_completed`, or `attention_required` on the pause
      path), because `:597` and `:500` are untouched. Add that assertion to the same test
      rather than a new one - a fix that leaves the run looking un-run is worse than the bug.
- [ ] 1.6 Run the gate and commit. If patch coverage is low, the recorder is probably not
      reaching the branch you changed - investigate rather than adding filler tests. Format
      before `git add`: the commit-time formatter is a writer while CI only asserts, so a
      commit can be pushed misformatted. Confirm `git status --porcelain` is empty afterwards.

## 2. The `score(overwrite=True)` publish is a valid document (red test + fix, one commit)

- [ ] 2.1 Reuse the 1.2 recorder against `score(overwrite=True)` on a workspace whose manifest
      status is `scored`. Assert every captured payload validates, and assert specifically that
      the document published at `:803` reports `run_completed`. Watch it fail with
      `manifest phase state is incomplete`.
- [ ] 2.2 Make it green: in the `if overwrite:` block of `score()` (`:796-800`), set
      `manifest["status"] = "run_completed"` after the pops. That is the status its survivors
      prove - prepare and run stay `completed`, and `live_checks.jsonl` is a `RUN_FILE` this
      prune never touches. Confirm the returned manifest still reports `scored`.
- [ ] 2.3 Run the gate and commit.

## 3. The staged live checks and their digest survive the window (red tests + fix, one commit)

- [ ] 3.1 Write the RED evidence test: drive
      `run(overwrite=True, authorize_staged_invalid=True)` on a paused run and inject an
      exception from the `observe_live_item` fake, which is the first call after the former
      unlink point at `:414` and is already faked in this suite (design.md D4 - it is the
      narrowest injection available; do not add a monkeypatch to production code to create a
      seam). Assert two things, not one: `live_checks.jsonl` is still on disk with its
      original contents, AND the last captured manifest payload still records that file's
      digest. Watch both halves fail.
- [ ] 3.2 Make it green with two edits inside the same `if overwrite:` block and one deletion:
      delete the `staged_checks_path.unlink()` line at `:414`, and stop popping
      `live_checks.jsonl` from `manifest["artifacts"]` when `authorize_staged_invalid` is set -
      mirroring the `owned - {"live_checks.jsonl"}` exclusion at `:355`. Do NOT add a sidecar
      file, a restore step, or a new entry in `OWNED_FILES`; design.md D2 records why
      `AtomicJsonlWriter`'s `os.replace` commit (`artifacts.py:113-129`) already makes the
      deletion sufficient, and what a sidecar would cost.
- [ ] 3.3 Add the counter-test in the same commit: `run(overwrite=True)` with
      `authorize_staged_invalid=False` must still remove `live_checks.jsonl` and its digest, as
      it does today. Without this case, "never delete the staged checks" is an easy over-fix
      that silently redefines what `--overwrite` means for every non-continue run (design.md
      D4).
- [ ] 3.4 Prove the replacement path is unbroken: a continued run that completes normally still
      writes a `live_checks.jsonl` whose digest matches the committed contents, and the
      pre-run rows from the staged copy are still carried into it (`:565-567`). An assertion on
      the row count is enough; the point is that removing the unlink did not leave the old file
      in place of the new one.
- [ ] 3.5 Run the gate and commit.

## 4. The invariant holds on the paths that were never broken (regression test, one commit)

- [ ] 4.1 Extend the 1.2 recorder over the full happy path - `prepare`, then `run` without
      `overwrite`, then `score` without `overwrite` - and assert every published
      `manifest.json` validates. This is the audit sweep the issue asks for, written as a test
      instead of a claim: it covers `:380`, `:502`, `:506`, `:600`, `:803` and `:881` in one
      pass and fails if a future edit republishes a mid-edit document.
- [ ] 4.2 Record, do not fix, the one finding the sweep turns up: `:862` sets
      `status = "scored"` in memory and `write_report` (`:864-870`) receives that manifest
      before `phases["score"]` is re-added at `:870`. Nothing persists it, so the on-disk
      invariant holds. Note it in the PR body as a latent issue that becomes real if
      `write_report` ever validates its argument (design.md D6). Do not change it here.
- [ ] 4.3 Run the gate and commit.

## 5. Verify against the acceptance criteria and open the PR

- [ ] 5.1 Walk the issue's four acceptance criteria one by one and name, for each, the test
      that covers it. Then confirm the third criterion literally: `git diff origin/dev --
      src/evaluation/qa/schema.py` is empty, and
      `pytest tests/unit/evaluation/qa/test_schema.py -q` passes with no test in that file
      modified. A criterion without a test is unfinished work, not a judgement call.
- [ ] 5.2 The project gate exits 0 from a clean tree, then push with
      `git push -u origin fix/issue-326-crash-safe-overwrite-manifest` (the `-u` matters: the
      branch was created from `origin/dev` and would otherwise track the trunk). Open the PR
      with `gh pr create --repo fasrc/archi --base dev`. Put `closes #326` in the PR **body** -
      a closing keyword in the title does not link the issue. No `Co-Authored-By` or session
      trailers.
- [ ] 5.3 In the PR body, lead with the measured before/after table from 1.1 and the injected-
      exception result from 3.1 - those two are the evidence. State plainly that this is a
      behaviour change on the observability surface: history and the console now show a
      mid-overwrite run as `prepared` or `run_completed` with its `runtime_phase`, instead of
      the `invalid` fallback row (`history.py:614`), and progress display is unaffected because
      `console.py:78-86` reads `runtime_phase` from the raw dict and never consults `status`.
      Say that `RunManifest.from_dict` is byte-identical. Name the deliberate exclusions
      (design.md, "Out of scope"): `_remove_owned` on a plain `--overwrite` at `:356`,
      `prepare(overwrite=True)`'s reset at `:217`, and the `write_report` finding from 4.2.
      Note that this defect is upstream's design in a `port-hunks` file from
      `archi-physics/archi` pin `bebfbe56`, found in post-merge review of PR #305, and that it
      needs reporting on `archi-physics/archi` PR #608 after this merges - file that as a
      follow-up issue rather than doing it from the PR. STOP at the open PR - do not merge.
