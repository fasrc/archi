# Tasks - fix-issue-326-crash-safe-overwrite-manifest

> Every numbered group below ends with the suite green and the project gate passing. A red
> test and the code that turns it green belong to the SAME group and the SAME commit,
> because a task that ends red can never be committed and the loop cannot proceed past it.
> Watch each red test fail mid-group and keep the output for the PR body.
>
> "The project gate" means the single documented gate command for this repo. Run it
> unmodified before every commit and never bypass it:
> `bash scripts/gate.sh`
>
> Line anchors are as of `origin/dev` at `7145888d` (re-verified 2026-10-21, and on `e2978a1d` 2026-10-22). Re-grep every
> anchor before you use it, and re-derive before citing any of them in the PR body - do not
> paste these numbers forward.
>
> `src/evaluation/qa/workflow.py`, `src/evaluation/qa/provenance.py`,
> `tests/unit/evaluation/qa/test_live_workflow.py` and `tests/unit/evaluation/qa/test_workflow.py`
> are black-clean under black 24.10.0 (verified 2026-10-21), so an in-place edit reflows no
> unrelated line and patch coverage is measured against the lines you actually wrote.
>
> Where the tests go (design.md D4): a NEW module,
> `tests/unit/evaluation/qa/test_overwrite_crash_safety.py`. Import the helpers from
> `tests.unit.evaluation.qa.test_live_workflow` - `_paused_at_live_gate` (`:177`), `_run`
> (`:158`) and the `runtimes` fixture (`:135`); precedent for importing a sibling test module
> is `test_jobs_history_wait_budget.py:12`. There is NO paused-run fixture and NO
> `observe_live_item` fake in `test_workflow.py` - do not look for one. Inject a mid-window
> failure with `monkeypatch.setattr(workflow_module, "observe_live_item", fake)`
> (`workflow.py:38`, called at `:440` and `:584`). Do NOT raise from the MCP invoker:
> `live_checks.py` catches that error and turns it into a check row. Every fake must state its
> stop condition: "raise on the first call, delegate to the real function after that". The
> first `observe_live_item` call of a continue is its PRE-RUN check (`:440`), before
> `answers.jsonl` is sealed; that is the injection point every test here uses (proposal.md
> known gap 3 explains why a later point shows a different history row).
> `SequenceInvoker` (`test_live_workflow.py:16`) pops one value per oracle call. When it is
> empty, its `IndexError` does NOT reach the test: `OracleResolver.resolve`
> (`src/evaluation/qa/oracle.py:437-458`) re-raises it as `OracleResolutionError`, and
> `observe_live_item` records an `oracle_failed` check row, so the run still completes on
> unintended live-check data. Pass enough values for every oracle call of every run: one
> live item costs one call at prepare, one pre-run and one post-run, so a `_run` followed by
> one overwrite needs 5 (for example `[{"value": 7, "revision": "r1"}] * 6`).
>
> Scope fences (design.md D5) - if you find yourself editing any of these, stop and reduce the
> change: `src/evaluation/qa/schema.py` (the issue forbids it), the terminal status
> assignments at `:512`, `:617`, `:889` and the publishes at `:514`, `:518`, `:620`, `:908`
> (already correct), `_remove_owned` (`:86-100`) and `OWNED_FILES` (design.md D2 exists to
> avoid them), `history.py` / `console.py` / `evaluations.js` (readers, not writers),
> `prepare()`, `provenance.py` beyond one docstring, and the two known gaps in proposal.md
> (recorded, not fixed).
>
> Turn boundary: each group is ONE checkbox, because the loop does one checkbox per turn and
> halts after two turns with no commit. The numbered sub-steps inside a group are prose
> instructions for that one turn, not separate tasks. Do not tick a group until its commit
> (or, for group 6, the open PR) exists.

## 1. D0: the start guard refuses before the overwrite deletes anything (red test + fix, one commit)

- [x] 1. Do steps 1.1-1.5 below in order, in ONE turn, ending with the gate green and ONE commit.

  - 1.1 Create `tests/unit/evaluation/qa/test_overwrite_crash_safety.py` with the helper
      imports described in the header. Write the RED test for acceptance criterion 1: build a
      COMPLETED run with `_run(monkeypatch, tmp_path, values, runtimes)` (copy the `values`
      of an existing `_run` caller in `test_live_workflow.py`; the run must end `scored`).
      Snapshot every file under the run dir as `{relative path: bytes}`. Monkeypatch
      `workflow_module.corpus_provenance.start_readings` to raise
      `CollectionNotReadyError("not ready")` (from `src.utils.benchmark_provenance`). Call
      `QAWorkflow().run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md",
      overwrite=True)` and assert it raises `CollectionNotReadyError`. Then assert the snapshot
      is identical: no file deleted, no file added, every byte the same, including
      `manifest.json`. Run it and watch it fail (today `_remove_owned` at `:362` deletes
      `answers.jsonl` and the score files first). Use a completed run, NOT a paused one:
      design.md D0 explains why a paused run cannot produce this red.
  - 1.2 Add the guard test for the trap in design.md D0, in the same module: monkeypatch
      `start_readings` to return sentinel values
      (`{"retrieval_identity": {"sentinel": "fresh"}, "corpus_fingerprint_before": "sha256/v2:fresh"}`),
      run `run(overwrite=True)` over a completed run, and assert the RETURNED manifest carries
      both sentinel values. This test passes today; it must still pass after 1.3. It fails if
      the whole `manifest.update(...)` line is moved above the block, because the prune pops
      every `PROVENANCE_KEYS` entry (`:368-369`).
  - 1.3 Make 1.1 green: directly after `config = redact_agent_config(config)` (`:357`), add
      `readings = corpus_provenance.start_readings(config, spec)`. Replace the line at `:385`
      with `manifest.update(readings)`, keeping it where it is (below the `if overwrite:`
      block). Do not move `load_agent_inputs` (already above the block) and do not change the
      prune.
  - 1.4 Correct the `start_readings` docstring in `src/evaluation/qa/provenance.py:79-82`. It
      says "no attempt has run, so nothing is lost", which was false on the overwrite path.
      Say instead that it raises `CollectionNotReadyError` from the guard, and that callers
      run it before they delete or write anything, so a refusal loses nothing. Docstring only.
  - 1.5 Run the gate and commit. Format before `git add`: the commit-time formatter is a
      writer while CI only asserts, so a commit can be pushed misformatted. Confirm
      `git status --porcelain` is empty afterwards.

## 2. D1 in `run()`: every published manifest is a valid document (red tests + fix, one commit)

- [x] 2. Do steps 2.1-2.6 below in order, in ONE turn, ending with the gate green and ONE commit.

  - 2.1 Confirm the defect first, so the red you see next is the right red. From the repo
      root, build a `qa-v2` manifest with `prepare`/`run`/`score` phases `completed` and the
      full digest map, apply the `:358-373` prune verbatim, and call
      `RunManifest.from_dict` for each prior status in
      `("scored", "run_completed", "attention_required")`. Expected: `scored` and
      `run_completed` raise `manifest phase state is incomplete`; `attention_required` raises
      `manifest attempts must be a positive integer`. Then set `status` to `"prepared"` on the
      same three and confirm all are accepted. Only TWO rules fire (the required-phases
      table, `schema.py:258-269`, and the attempts check, `:271-275`); the qa-v2 live-check
      rule (`:300-310`) is never reached. Keep the before/after table for the PR body. This
      step writes no file; step 5.1 turns the table into a permanent test.
  - 2.2 In the new test module, add a recorder helper that monkeypatches `write_json` in the
      `src.evaluation.qa.workflow` module namespace, and for every call whose path name is
      `manifest.json` stores `copy.deepcopy(payload)` before delegating to the real function.
      The deep copy is not optional (design.md D4): the workflow mutates one dict for the
      length of the call, so a stored reference reads back at its final value and the test
      passes against the unfixed code. Do NOT put a length assertion in the helper: the 4.1
      test captures exactly ONE document (`:392`) before its injected raise, so a shared
      "more than one" check would make 4.1 fail for a reason unrelated to the fix. Put the
      "captured more than one document" assertion in the 2.3, 2.3a, 3.1 and 3.3 tests.
  - 2.3 Write the RED test for acceptance criteria 2 and 4 on the continue path: start from
      `_paused_at_live_gate(monkeypatch, tmp_path)`, install the recorder, then call
      `workflow.run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md", overwrite=True,
      pause_on_live_mismatch=True, authorize_staged_invalid=True)` (the shape of
      `test_continue_accepts_frozen_agent_inputs_rewritten_byte_for_byte`). Assert every
      captured payload round-trips through `RunManifest.from_dict`, and that the RETURNED
      manifest reports `run_completed` (this continue does not pause again: its only live
      item is authorized). Watch it fail on the document published at `:392`.
  - 2.3a Write the RED test for acceptance criteria 2 and 4 on the plain `--overwrite` path
      (the CLI re-run, `src/cli/qa_eval.py:222-230`, which never passes
      `authorize_staged_invalid`): build a COMPLETED run with `_run` and enough invoker values
      for both runs (see the header), install the recorder, and call
      `QAWorkflow().run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md",
      overwrite=True)`. Assert every captured payload round-trips through
      `RunManifest.from_dict` and the RETURNED manifest reports `run_completed`. Watch `:392`
      and `:518` fail with `manifest phase state is incomplete`. Without this test, a D1 line
      wrapped in `if authorize_staged_invalid:` passes every other test in this file.
  - 2.4 Write the RED test for the spec scenario "A failure mid-overwrite leaves a workspace
      that still loads" (acceptance criterion 5, second half): from `_paused_at_live_gate`,
      monkeypatch `workflow_module.observe_live_item` with a fake that raises
      `RuntimeError("injected")` on its first call (the continue's pre-run check, `:440`) and
      delegates to the real function after that. Call the same continue and assert it raises
      `RuntimeError`. Then assert `RunManifest.from_dict(read_json(run_dir / "manifest.json"))`
      succeeds. Select the history row by id, because the `invalid` fallback row and a valid
      row carry different names (`history.py:564-567` vs `:611-617`): `history =
      EvaluationHistory(tmp_path)`, then `(row,) = [r for r in history.list_runs() if r["id"]
      == history.id_for_path(run_dir)]`. Assert `row["status"] == "prepared"` and
      `row["valid"] is True`, not the `invalid` fallback row (`history.py:614`). Watch it fail.
  - 2.5 Make all three green with the smallest change: in the `if overwrite:` block of `run()`
      (`:358-373`), set `manifest["status"] = "prepared"` after the pops, unconditionally (not
      under `authorize_staged_invalid`). Do not touch the pops themselves, the
      `runtime_phase` assignment at `:387-391`, or the publish at `:392`. design.md D1 has
      the argument for `prepared` specifically - `attempts` is popped, and it is the only
      status that tolerates its absence.
  - 2.6 Run the gate and commit. If patch coverage is low, the recorder is probably not
      reaching the branch you changed - investigate rather than adding filler tests.

## 3. D1 in `score()`, and the overwrite re-pause publish (red test + fix, one commit)

- [x] 3. Do steps 3.1-3.4 below in order, in ONE turn, ending with the gate green and ONE commit.

  - 3.1 Reuse the 2.2 recorder against `QAWorkflow().score(run_dir, overwrite=True)` on a
      completed run whose manifest status is `scored` (`_run`). Assert every captured payload
      validates, and assert that the document published at `:823` reports `run_completed`.
      Watch it fail with `manifest phase state is incomplete`.
  - 3.2 Make it green: in the `if overwrite:` block of `score()` (`:816-820`), set
      `manifest["status"] = "run_completed"` after the pops. That is the status its survivors
      prove - prepare and run stay `completed`, and `live_checks.jsonl` is a `RUN_FILE` this
      prune never touches. Assert the RETURNED manifest still reports `scored` (acceptance
      criterion 4).
  - 3.3 Add the test for acceptance criteria 2 and 4 on the `run()` overwrite RE-PAUSE path
      (the only path that publishes `:514` during an overwrite, and the only one that returns
      `attention_required`). `_dataset` builds one live item, so add a local dataset helper
      in the new module that writes the same shape with TWO live items (copy the `live` item
      as `live-b` with question `"Other value?"`) plus the static item. Invoker values, one
      per oracle call in this order: prepare `live`, prepare `live-b`, pre-run `live`,
      pre-run `live-b`, then the continue's pre-run `live`, pre-run `live-b`. Use
      `{"value": 7, "revision": "r1"}`, `{"value": 7, "revision": "r1"}`,
      `{"value": 8, "revision": "r2"}`, `{"value": 7, "revision": "r1"}`,
      `{"value": 8, "revision": "r2"}`, `{"value": 9, "revision": "r3"}`, so the first run
      pauses on `live` and the continue pauses again on `live-b`. Drive the first run with
      `composite(..., pause_on_live_mismatch=True)` and assert it returns
      `attention_required`. If it does not, print the invoker's `calls` and adjust the value
      order, not the assertion. Then install the recorder and call the continue as in 2.3.
      Assert every captured payload validates and the RETURNED status is
      `attention_required`. The group 2 fix already makes this test pass, so prove it is a
      real test: comment out the group 2 status line, watch the test fail with `manifest
      attempts must be a positive integer` (both captured documents then carry
      `attention_required`), and restore the line before you commit.
  - 3.4 Run the gate and commit.

## 4. D2 + D3: the staged live checks and their digest survive the window (red tests + fix, one commit)

- [x] 4. Do steps 4.1-4.6 below in order, in ONE turn, ending with the gate green and ONE commit.

  - 4.1 Write the RED evidence test for acceptance criterion 3: from `_paused_at_live_gate`,
      record the bytes of `run_dir / "live_checks.jsonl"`, install the 2.2 recorder, and
      install the raising `observe_live_item` fake from 2.4 (raise on the first call,
      delegate after that). Run the continue and assert it raises. The recorder captures
      exactly ONE document here (`:392`), because the raise at `:440` comes before `:518`.
      Assert two things, not one: `live_checks.jsonl` is still on disk with exactly the
      recorded bytes, AND that captured manifest payload records that file's digest, equal to
      `sha256_file` of it. Watch both halves fail.
  - 4.2 Write the RED test for the spec scenario "a subsequent continue passes its input
      verification" (acceptance criterion 5, first half): after the injected failure of 4.1
      (the fake now delegates), call the same continue again. Assert it does not raise and
      returns a manifest whose status is `run_completed` or `attention_required`. Today it
      fails on the missing staged file or its missing digest. Watch it fail.
  - 4.3 Make both green with one deletion and one edit, inside `run()`: delete the
      `staged_checks_path.unlink()` line at `:426`, and in the prune pop `owned` instead of
      `RUN_FILES | SCORE_FILES` from `manifest["artifacts"]` (`:372-373`), so the digest of
      the kept file stays. Do NOT add a sidecar file, a restore step, or a new entry in
      `OWNED_FILES`; design.md D2 records why `AtomicJsonlWriter`'s `os.replace` commit
      (`artifacts.py:113-129`) already makes the deletion sufficient.
  - 4.4 Add the counter-test in the same commit (acceptance criterion 3, second half):
      `run(overwrite=True)` with `authorize_staged_invalid=False` over a completed run must
      still remove `live_checks.jsonl` and its digest, as it does today. Make the run raise
      before the new file is sealed (the 2.4 fake works) so the assertion sees the
      post-prune state, not a rewritten file. Without this case, "never delete the staged
      checks" is an easy over-fix that silently redefines `--overwrite` (design.md D4).
  - 4.5 Prove the replacement path is unbroken: a continued run that completes normally still
      writes a `live_checks.jsonl` whose digest in the returned manifest matches the
      committed contents, and the pre-run rows are still carried into it (`:577-579`). An
      assertion on the row count is enough. Extend the 3.3 re-pause test too: its last
      captured payload (the `:514` publish) records a `live_checks.jsonl` digest equal to
      `sha256_file` of the file on disk (the recomputation at `:486-495`). For the spec
      clause "at no point during the write does the path hold a partial file or no file",
      cite the existing `tests/unit/evaluation/qa/test_artifacts.py::TestAtomicJsonlWriter::test_failure_preserves_previous_artifact`
      (the `AtomicJsonlWriter` contract) instead of writing a new test.
  - 4.6 Run the gate and commit.

## 4a. The recovery continue still checks the frozen agent inputs (red test + fix, one commit)

> Added by the post-PR review of 2026-10-22 (3 of 3 finders, 2 of 2 verifiers, reproduced on a
> copy of the branch with the 4.3 edit applied). Group 4 makes the later continue of 4.2
> possible, but the prune still pops the digests of `agent_config.resolved.yaml` and
> `agent_spec.resolved.md` (both are in `owned`), and nothing records them again before the
> pause or completion publish. The check at `workflow.py:318-322` verifies those two files only
> when their digest is in `manifest["artifacts"]`. So the later continue skips it, and an edit
> made after the failure is re-sealed. That is what
> `test_live_workflow.py::test_continue_refuses_tampered_frozen_agent_inputs` exists to stop.
> Do this group only after the group 4 commit exists: before group 4, the later continue fails
> earlier on the missing `live_checks.jsonl`, so the red test cannot show this defect.

- [x] 4a. Do steps 4a.1-4a.4 below in order, in ONE turn, ending with the gate green and ONE commit.

  - 4a.1 Write the RED test (spec scenario "An exception after the read leaves the evidence
      intact", last AND). Parametrize it over `"agent_config.resolved.yaml"` and
      `"agent_spec.resolved.md"`. From `_paused_at_live_gate`, install the raising
      `observe_live_item` fake from 2.4 (raise on the first call, delegate after that) and run
      the continue so that it raises. Then append `"\ntampered: true\n"` to that file in
      `run_dir`, the same tamper as `test_continue_refuses_tampered_frozen_agent_inputs`
      (re-grep it in `test_live_workflow.py`), and call the same continue again with the same
      arguments that test uses. Assert `str(caught.value) == f"workspace artifact hash mismatch: {artifact}"`.
      Watch it fail: after group 4 the later continue does not raise.
  - 4a.2 Make it green with the minimum code, inside `run()`: right after `spec_hash` is
      computed (re-grep `spec_hash = sha256_file`), add
      `if authorize_staged_invalid:` and record `config_hash` and `spec_hash` in
      `manifest["artifacts"]` under `"agent_config.resolved.yaml"` and
      `"agent_spec.resolved.md"`, so the first publish of a continue names the agent inputs
      it rewrote. Key it off the same flag as the `live_checks.jsonl` retention (design.md D3).
      Do not keep the paused run's old digests instead. Do not change the check at `:318-322`.
      The 4.2 test (an untampered later continue completes) and
      `test_continue_refuses_tampered_frozen_agent_inputs` must stay green.
  - 4a.3 In `tests/unit/evaluation/qa/test_overwrite_crash_safety.py`, remove each of the
      imports `deque`, `CallToolResult` and `OracleCallEvidence` that is still unused
      (`grep -c` of the name in the file is 1). CI runs no unused-import check, so nothing
      else catches them.
  - 4a.4 Run the gate and commit.

## 5. The validator fence, and the invariant on the paths that were never broken (regression tests, one commit)

- [x] 5. Do steps 5.1-5.4 below in order, in ONE turn, ending with the gate green and ONE commit.

  - 5.1 Turn the 2.1 table into a permanent test (the spec scenario "The schema validator is
      not weakened", second THEN). In the new module, add a test parametrized over the prior
      status in `("scored", "run_completed", "attention_required")`: build a real `qa-v2`
      manifest with `_run` (it ends `scored`) and set the status for the other two, apply
      the `:358-373` prune to a deep copy, and assert `RunManifest.from_dict` refuses it with
      the exact message: `manifest phase state is incomplete` for `scored` and
      `run_completed`, `manifest attempts must be a positive integer` for
      `attention_required`. In the same test, set `status` to `"prepared"` on the pruned copy
      and assert it is accepted. Add the `score()` prune case (`:816-820`): `scored` is
      refused with `manifest phase state is incomplete`, and demoted to `run_completed` it is
      accepted. The test passes on arrival: it pins the validator, not the writer, and it
      fails if a later edit widens `from_dict` instead of fixing the writer.
  - 5.2 Extend the 2.2 recorder over the full happy path - `prepare`, then `run` without
      `overwrite`, then `score` without `overwrite` (the `_run` helper drives all three) - and
      assert every published `manifest.json` validates. This is the audit sweep the issue
      asks for, written as a test instead of a claim: it covers `:285` (prepare), `:392`,
      `:518`, `:620`, `:823` and `:908` in one pass, and fails if a future edit republishes a
      mid-edit document. `:514` (the pause publish) is NOT reached by this path; the 3.3
      re-pause test covers it on the overwrite path. Add one more case here for the
      first-run pause: install the recorder BEFORE calling `_paused_at_live_gate`, and assert
      the captured `attention_required` document validates.
  - 5.3 Record, do not fix, the one finding the sweep turns up: `:889` sets
      `status = "scored"` in memory and `write_report` (`:891-896`) receives that manifest
      before `phases["score"]` is re-added at `:897`. Nothing persists it. Keep the note for
      the PR body (design.md D6). Do not change it here.
  - 5.4 Run the gate and commit.

## 6. Verify against the acceptance criteria and open the PR

- [x] 6. Do steps 6.1-6.3 below in order, in ONE turn: verify, push, and open the PR. STOP at the open PR.

  - 6.1 Walk the issue's eight acceptance criteria BY NUMBER and name, for each, the test or
      command that covers it: (1) the 1.1 test; (2) the 2.3, 2.3a, 3.1 and 3.3 tests; (3)
      the 4.1 and 4.4 tests; (4) the returned-status assertions of 2.3, 2.3a, 3.2 and 3.3;
      (5) the 4.2, 4a.1 and 2.4 tests; (6) `git diff origin/dev -- src/evaluation/qa/schema.py tests/unit/evaluation/qa/test_schema.py`
      is empty, `pytest tests/unit/evaluation/qa/test_schema.py -q` passes, and the 5.1
      validator test passes; (7) every
      checkbox in this file is `[x]` once this group is done, and
      `openspec validate fix-issue-326-crash-safe-overwrite-manifest --strict` passes;
      (8) the gate exits 0 from a clean tree. A criterion without evidence is unfinished
      work, not a judgement call. Print the list into the PR body.
  - 6.2 Push with `git push -u origin fix/issue-326-crash-safe-overwrite-manifest`. PR #741
      already exists for this branch (the loop opened it after group 1), so do NOT run
      `gh pr create`: write the body to a file and run
      `gh pr edit 741 --repo fasrc/archi --title "<summary>" --body-file <file>`. Put
      `Closes #326` in the PR **body** - a closing keyword in the title does not link the
      issue. Remove the "Not ready to merge" banner. No `Co-Authored-By` or session trailers.
  - 6.3 In the PR body, lead with the measured before/after table from 2.1 and the results of
      the 1.1 and 4.1 red tests - those are the evidence. State plainly that this is a
      behaviour change on the observability surface: history and the console now show a
      mid-overwrite run as `prepared` or `run_completed` instead of the `invalid` fallback row
      (`history.py:614`). In `run()` the history list shows `invalid` again from the
      `answers.jsonl` seal to the next manifest write. In `score()` the list row stays
      `run_completed` until `summary.json` is written, and only the history detail view
      (`get_run`) refuses the run from the `evaluation_results.jsonl` seal onward (known gap 3,
      which exists today on first runs too; re-grep the `history.py` anchors). `progress.status` changes with it (`console.py:82` copies the manifest status),
      but the display does not change while a phase runs, because `evaluations.js:62-64`
      shows `runtime_phase` first. Say that `RunManifest.from_dict` is byte-identical. Name
      every known gap and the known limitation from proposal.md (the end-of-run provenance
      window, accepted by the operator; the delete-to-publish windows in `run()` and
      `score()`; the seal-to-publish windows; and that after a failed continue the console
      cannot issue a new continue and the CLI has no authorize flag) and the deliberate
      exclusions (plain `--overwrite` deleting `live_checks.jsonl`, `prepare(overwrite=True)`'s
      reset, the `write_report` note from 5.3). Note that the same defect exists upstream on `archi-physics/archi` `main` and that
      a human decides after merge whether to report it there - do not post upstream. Then
      tick this checkbox, commit the tick, and push again so the branch tip holds it. STOP at
      the open PR - do not merge.
