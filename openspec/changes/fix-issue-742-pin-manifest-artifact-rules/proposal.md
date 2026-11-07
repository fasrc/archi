## Why

`RunManifest.from_dict` (`src/evaluation/qa/schema.py:300-314` on `origin/dev` `73104693`) has
two artifact rules:

- A `qa-v2` manifest (`SCHEMA_VERSION`, `src/evaluation/qa/constants.py:12`) whose status is
  `attention_required`, `run_completed` or `scored`, and whose `artifacts` has no
  `live_checks.jsonl`, raises `ValueError("qa-v2 manifest is missing live-check artifacts")`.
- A `scored` manifest whose `artifacts` does not hold both `summary.json` and `report.md`
  raises `ValueError("scored manifest is missing result artifacts")`.

No test pins either rule. `git grep -n -E 'missing live-check artifacts|missing result
artifacts' -- tests src` finds only the two `raise` lines. The #326 fence test
`TestValidatorFence` (`tests/unit/evaluation/qa/test_overwrite_crash_safety.py:484-542`) pins
only the phases rule and the attempts rule. The 2026-10-22 review of PR #741 measured it: with
both rules disabled in a copy, the unit suite gave the same result as the unchanged copy.

The #326 scenario "The schema validator is not weakened" says a manifest that claims a terminal
status without the artifacts that status requires is still refused. Today a later edit can
remove both artifact rules and every test stays green.

## What Changes

- Add 6 test cases to `TestValidatorFence` in
  `tests/unit/evaluation/qa/test_overwrite_crash_safety.py`:
  - 3 cases on a real `scored` run manifest, one for each of `live_checks.jsonl`,
    `summary.json` and `report.md`. Each pops one artifact and asserts the exact message.
  - 2 cases for the live-check rule on the edited statuses `run_completed` and
    `attention_required`.
  - 1 case for the live-check rule on the real first-run pause manifest
    (`_paused_at_live_gate`).
- Each case first asserts that the unchanged document is accepted (a positive control), so a
  pass cannot come from an earlier rule.
- A mutation check in a throwaway copy proves each case is real: with the live-check rule
  disabled, exactly the 4 live-check cases fail; with the scored rule disabled, exactly the 2
  result-artifact cases fail.

No source file changes. `src/evaluation/qa/schema.py` is not edited, and the text of the #326
scenario is not edited.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `qa-evaluation-trial`: adds a requirement that names the two artifact rules of the
  run-manifest validator and the tests that pin them.

## Impact

- Tests only: `tests/unit/evaluation/qa/test_overwrite_crash_safety.py`.
- No runtime, API, config or docs change. A tests-only diff scores no patch-coverage lines.
- Closes #742.
