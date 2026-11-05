## Context

`RunManifest.from_dict` (`src/evaluation/qa/schema.py:234-346` on `origin/dev` `73104693`)
checks a run manifest in this order: schema version, run id, status, phases (`:255-269`),
attempts (`:271-275`), artifact entries (`:277-287`), input and preparation artifacts
(`:289-299`), the live-check rule (`:300-310`), and the result-artifact rule (`:311-314`).

`TestValidatorFence` (`tests/unit/evaluation/qa/test_overwrite_crash_safety.py:484-542`) is the
fence of #326. It pins the phases rule and the attempts rule. It has no case for the two
artifact rules. PR #741 merged on 2026-10-23, so the fence class is on `origin/dev`.

## Goals / Non-Goals

**Goals:** a test fails if a later edit removes or weakens either artifact rule; each test
proves that the document it breaks was valid before the break.

**Non-Goals:**
- Any change to `src/evaluation/qa/schema.py` or to another source file.
- Any change to the text of the #326 scenario "The schema validator is not weakened"
  (`openspec/changes/fix-issue-326-crash-safe-overwrite-manifest/specs/qa-evaluation-trial/spec.md:87-92`).
- Rules for legacy `qa-v1` manifests. The live-check rule applies to `qa-v2` only.

## Decisions

### D1. Put the cases in `TestValidatorFence`

The issue names that class. It already imports `copy`, `read_json`, `SCORE_FILES`,
`RunManifest`, `_run`, `_paused_at_live_gate` and the `runtimes` fixture
(`test_overwrite_crash_safety.py:3-21`), so the new cases need no new import. Insert them after
`test_score_prune_refused_with_wrong_status` and before `class TestHappyPathAuditSweep`.

### D2. Assert the exact message

Each case uses `pytest.raises(ValueError) as caught` and then
`assert str(caught.value) == message`. A `match=` regex accepts a longer or a different message
that contains the text. Both rules raise `ValueError`, so the message is the only thing that
tells them apart.

### D3. Build each document from a real run

- **Scored:** `_run(monkeypatch, tmp_path, [{"value": 7, "revision": "r1"}] * 3, runtimes)`
  ends `scored` with schema `qa-v2`, the same as `test_score_prune_refused_with_wrong_status`.
  Popping `live_checks.jsonl` triggers the live-check rule first (it runs before the scored
  rule). Popping `summary.json` or `report.md` triggers the result-artifact rule.
- **`run_completed`:** the scored document with `status = "run_completed"`, the `score` phase
  removed, and the `SCORE_FILES` artifacts removed. That passes every earlier rule.
- **`attention_required`:** the smallest edit, `status = "attention_required"` on the scored
  document. Its required phase is only `prepare` (`schema.py:260`) and it keeps `attempts`, so
  it passes every earlier rule.
- **Real pause:** `_paused_at_live_gate(monkeypatch, tmp_path)` drives a real first run to the
  live-mismatch gate. Its manifest is the document the code writes for `attention_required`.
  This case keeps the edited `attention_required` case honest.

Each case asserts the unchanged document is accepted before it breaks it (positive control).

### D4. Prove each case with a mutation check

The new tests pass on arrival, because they pin current behaviour. The red step of TDD is a
mutation check in a throwaway copy (Appendix B of `tasks.md`): add `and False` to the
live-check condition, then `False and` to the scored condition, one at a time. The copy takes
`HEAD` from `git archive` and the test file from the working tree, so it works before and after
the commit. Never mutate the real checkout.

Loop 1 ran Appendix B on `origin/dev` `73104693` with the Appendix A tests added:

- Fence green: `10 passed, 15 deselected`. Whole file green: `25 passed`.
- Live-check rule disabled: `4 failed, 6 passed, 15 deselected`. The 4 failures are the 4
  live-check cases.
- Scored rule disabled: `2 failed, 8 passed, 15 deselected`. The 2 failures are the
  `summary.json` and `report.md` cases.

### D5. Spec delta uses ADDED

The #326 change is not archived, so its requirements are not in
`openspec/specs/qa-evaluation-trial/spec.md` yet. A `MODIFIED` delta of a requirement that the
main spec does not hold fails at archive time. This change adds one new requirement instead.

## Risks / Trade-offs

- **The `run_completed` and `attention_required` documents are edited, not written by the
  code.** D3's real pause case covers the real `attention_required` document. A later change
  that adds a status-specific rule can make an edited document invalid; the positive control
  then fails first and names the cause.
- **Each `_run` call drives a full fake run.** The 6 cases add about 0.2 s to the file.
