## 1. Red test (TDD)

- [x] 1.1 Create `tests/unit/test_doc_anchor_guard.py`. Module docstring states the policy and
  the two spellings: `` [`app.py:NNNN`][tag] `` is canonical and MUST equal `[tag]`'s link
  definition; `` [`:NNNN`][tag] `` is abbreviated and MAY cite another line in the same
  region. Include the line: "A red result means the doc anchors drifted — update
  docs/docs/api_reference.md, not this test." Read both files with `pathlib.Path`, following
  `tests/unit/test_require_auth.py::TestNoUnreachableStatementRemains`; import nothing from
  `src`.
- [x] 1.2 Add the parsers. `_link_definitions(text)` returns `{tag: line_no}` from
  `^\[([^\]]+)\]:\s*https://github\.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app\.py#L(\d+)\s*$`
  (30 tags at `origin/dev` 0a157cdc). `_inline_citations(text)` returns one record per
  citation — `(doc_line, spelling, start, end_or_None, tag)` — matching
  `` [`app.py:N`][tag] ``, `` [`:N`][tag] ``, and `` [`app.py:N-M`][tag] `` (42 citations:
  24 canonical, 17 abbreviated, 1 range).
- [x] 1.3 Add the **expected-substring table** as a module-level dict `{line_no: substring}`
  covering all 33 distinct anchored line numbers. Derive each from the current content of
  `src/interfaces/chat_app/app.py`, choosing the most distinctive short fragment (a dict key
  with its literal, a whole `if` condition), not the whole line — see design.md Decision 4.
  Two entries collide by content (`2418` and `2432` are both `if include_tool_steps:`); keep
  both, the assertion is positional. Enter `2143` (not `2138`) for `[ovrwarn2]` — the
  substring is the warning `yield`, which is the assertion that must go red in 1.6.
- [ ] 1.4 Test: **content assertion.** For every line number appearing in a link definition
  or an inline citation (both range endpoints included), assert `app.py`'s line at that
  number contains the table's substring. On failure report tag, doc line, line number,
  expected substring, and the actual line content.
- [ ] 1.5 Test: **table completeness.** Assert the table's key set equals the set of line
  numbers actually cited, so adding an anchor without a table entry fails rather than being
  skipped. Assert every parsed citation's tag resolves to a known link definition, and that
  the counts hold (30 definitions, 42 citations) so a citation form the parsers do not
  recognise fails loudly instead of being silently dropped.
- [ ] 1.6 Run `python -m pytest tests/unit/test_doc_anchor_guard.py -q` and confirm it FAILS
  naming `[ovrwarn2]` — the doc says line 2138 (a comment tail) where the table expects the
  warning `yield`. **Watch it go red before touching the doc.** This is the red step; the
  drift is real, not synthetic.

## 2. Consistency assertions

- [ ] 2.1 Test: **canonical citations match their definition.** For every
  `` [`app.py:NNNN`][tag] ``, assert `NNNN` equals `[tag]`'s link-definition line. All 24
  pass at `origin/dev`; the test is the guard against a future disagreement.
- [ ] 2.2 Test: **abbreviated citations are exempt from equality.** Assert the three known
  deliberate divergences are accepted — `` [`:2412`][thinkgate] `` (def 2418),
  `` [`:2424`][thinkgate2] `` (def 2432), `` [`:1773`][stepemit] `` (def 1767) — so a later
  edit cannot quietly tighten the rule into failing correct documentation (design.md
  Decision 1).
- [ ] 2.3 Test: **range endpoints.** For `` [`app.py:2435-2441`][chunkyield] ``, assert both
  endpoints are content-verified and that `[chunkyield]`'s definition (2441) equals one
  endpoint.

## 3. Repair the drift

- [ ] 3.1 In `docs/docs/api_reference.md:183`, repoint `[ovrwarn2]` from `#L2138` to `#L2143`
  — the `yield {` that opens the `{"type": "warning", "message": "Using default model: …"}`
  event for a failed request-local pipeline build. Update the inline citation at `:166`
  (`` [`:2138`][ovrwarn2] ``) to `` [`:2143`][ovrwarn2] `` to match.
- [ ] 3.2 Re-run the test. Every content assertion now passes. If any *other* anchor turns
  out stale, repair the document the same way — never loosen the table to accommodate it.
- [ ] 3.3 Confirm the doc edit is anchors-only: `git diff docs/docs/api_reference.md` shows
  no prose changes.

## 4. Prove the guard catches a shift

- [ ] 4.1 Insert a blank line near the top of `ChatWrapper.stream` in
  `src/interfaces/chat_app/app.py`. Run `python -m pytest tests/unit/test_doc_anchor_guard.py -q`
  and confirm it fails naming at least one specific stale anchor with expected-vs-actual.
- [ ] 4.2 Revert that line: `git checkout -- src/interfaces/chat_app/app.py`. Confirm
  `git status --porcelain` lists no change to `app.py` and the test is green again. **The
  branch must contain no change to `app.py`** — this task is a demonstration, not an edit.
  Record the observed failure message in the PR body as the acceptance-criteria evidence.

## 5. Document the policy

- [ ] 5.1 In `AGENTS.md`, under `## Testing Guidelines`, add the rule: a change that shifts
  lines in `src/interfaces/chat_app/app.py` must update the matching anchors in
  `docs/docs/api_reference.md`, which `tests/unit/test_doc_anchor_guard.py` enforces in the
  gate. State the two spellings and when to use each. Do not touch any other section.

## 6. Verify green + gate

- [ ] 6.1 `python -m pytest tests/unit/test_doc_anchor_guard.py -q` — green.
- [ ] 6.2 Non-vacuity check: corrupt one table substring, confirm exactly that anchor fails,
  restore it. A guard that passes because it asserts nothing is the failure mode here.
- [ ] 6.3 Format before staging: run `black` and `isort` on the new test file, then
  `git add`. Confirm `git status --porcelain` is empty afterwards — the pre-commit hook is a
  writer while CI is an assert, so a file formatted *after* staging is pushed misformatted
  and CI goes red.
- [ ] 6.4 Run the gate bare from the repo root — `cd` to the worktree first, and never pipe
  or redirect its output (the wrapper refuses a piped invocation, and a run from the wrong
  cwd can pass against the wrong checkout). Confirm exit 0 and diff coverage ≥80% on the
  changed lines specifically, not merely a passing total. The `.md` edits are not Python and
  do not enter the coverage measurement.

## 7. Ship

- [ ] 7.1 Commit (short lowercase subject, no `Co-Authored-By`) and push with
  `git push -u origin fix/issue-190-doc-anchor-guard` — the branch was cut from `origin/dev`,
  so set its upstream explicitly rather than relying on the tracking it inherited.
- [ ] 7.2 Open the PR against `fasrc/archi:dev` with `closes #190` **in the body** (a closes
  keyword in the title does not link the issue). In the body: the `[ovrwarn2]` drift the
  guard found and fixed, the two-spelling rule and why equality alone would have failed three
  correct refs (design.md Decision 1), and the 4.1 failure output as evidence for the
  acceptance criterion. Do **not** merge.
