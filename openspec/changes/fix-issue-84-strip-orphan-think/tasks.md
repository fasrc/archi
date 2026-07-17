## 1. Red — failing tests first

- [x] 1.1 Create `tests/unit/test_base_react_thinking_parse.py` that exercises
  `BaseReActAgent._parse_thinking_content` directly (instantiate the agent, or call the
  method with a minimal dummy `self`). Cover: balanced-pair regression
  (`"<think>r</think>\n\nAns"` → visible `"Ans"`, `"r"` in thinking); single orphan
  (`"reasoning\n</think>\n\nAns"` → visible `"Ans"`); multiple orphans
  (`"t1\n</think>\n\nt2\n</think>\n\nt3\n</think>\n\nAns"` → visible `"Ans"`); no tags
  (`"Just an answer."` unchanged, thinking empty); empty string → `("", "")`. For every
  leaked-content fixture assert `"</think>" not in visible and "<think>" not in visible`.
- [x] 1.2 Run `python -m pytest tests/unit/test_base_react_thinking_parse.py -q` and
  confirm the orphan cases FAIL against the current implementation (proves the bug).

## 2. Green — minimal fix

- [x] 2.1 In `src/archi/pipelines/agents/base_react.py`, modify `_parse_thinking_content`
  only: after removing balanced `<think>…</think>` pairs, if any `</think>` remains, treat
  everything up to and including the LAST remaining `</think>` as thinking and keep only
  the text after it as visible; accumulate the removed reasoning into `thinking_content`.
  Do not change the signature, callers, or surrounding code.
- [x] 2.2 Run `python -m pytest tests/unit/test_base_react_thinking_parse.py -q` and
  confirm all cases pass.

## 3. Gate & verify

- [x] 3.1 Run the full unit suite `python -m pytest tests/unit/` — no regressions.
- [x] 3.2 Confirm formatting is clean: `black --check src tests scripts` and
  `isort --check-only src tests scripts` exit 0 (diff confined to the method body + new
  test file).
- [x] 3.3 Run the full gate `bash scripts/gate.sh` — must exit 0, including diff-cover
  ≥80% (target 100%) on the changed `base_react.py` lines.
- [ ] 3.4 Open the PR against `fasrc/archi:dev`; body links issue #84. Do not merge.
