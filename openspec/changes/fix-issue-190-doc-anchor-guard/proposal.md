## Why

`docs/docs/api_reference.md` documents the chat app by pointing at exact lines of
`src/interfaces/chat_app/app.py` — a 7099-line file. At `origin/dev` (0a157cdc) it carries
**72 anchor occurrences**: 30 reference-link definitions of the form
`[tag]: https://github.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app.py#L<n>`
(doc `:45-46`, `:105-106`, `:181-201`, `:268-269`, `:350-353`) and 42 inline citations of
those tags, resolving to **33 distinct line numbers**. Nothing verifies any of them. A PR
that shifts lines in `app.py` — the routine outcome of almost any edit to it — silently
repoints every anchor below the shift at unrelated code, and the gate stays green.

**This has already happened.** `[ovrwarn2]` (doc `:183`) is stale today: it claims
`app.py:2138`, which the table at doc `:166` cites as a source of the
`{"type": "warning", "message": "Using default model: …"}` event. Line 2138 is the tail of a
comment (`# pipeline — no unwind needed.`); the `yield` it documents is at `:2143-2145`, five
lines further down. A reader following that link lands on prose about unwinding and finds no
warning at all. This is a present-tense defect found while grounding this change, not a
hypothetical.

`app.py` measures ~18% line coverage and no test reads `api_reference.md`, so drift has no
detector. The precedent for fixing that already exists in-repo:
`tests/unit/test_require_auth.py::TestNoUnreachableStatementRemains` parses `app.py` as
source text and asserts a structural property of it without importing or calling the app.

## What Changes

- Add `tests/unit/test_doc_anchor_guard.py`, which the gate already runs
  (it invokes `python -m pytest tests/unit/`), holding a checked-in table that maps each
  anchored line number to the substring the line at that number must contain. A drifted
  anchor fails the gate and the failure message names the stale tag, the doc line, the
  expected substring, and what is actually there.
- Verify **every** anchor occurrence, both link definitions and inline citations, against
  `app.py` — not just the 30 definitions. This is stronger than checking definitions alone,
  because 3 inline citations legitimately point at lines their definition does not.
- Enforce inline/definition agreement **only for the canonical `` [`app.py:NNNN`][tag] ``
  spelling**, which is what the issue's acceptance criterion asks for and what all 24 such
  refs satisfy today. The abbreviated `` [`:NNNN`][tag] `` spelling is deliberately allowed
  to cite a different line than its tag's definition — see design.md; enforcing equality
  there would fail 3 correct refs and "fixing" them would corrupt the documentation.
- Repair the one genuinely stale anchor (`[ovrwarn2]`).
- Write the policy down in `AGENTS.md`: a PR that shifts lines in `app.py` must update the
  anchors in `api_reference.md`.

## Capabilities

### New Capabilities
- `doc-anchor-integrity`: line-number references from the documentation into
  `src/interfaces/chat_app/app.py` are verified by the gate against the code they name, so
  a change that shifts lines fails loudly instead of silently repointing the docs.

### Modified Capabilities
<!-- None. No existing capability in openspec/specs/ governs documentation anchors. -->

## Impact

- **Tests:** new `tests/unit/test_doc_anchor_guard.py`. Pure addition; reads two files from
  disk, imports nothing from `src`, and runs in well under a second.
- **Docs:** `docs/docs/api_reference.md` — repair `[ovrwarn2]`'s line number (and any other
  anchor the new table proves stale). No anchor *format* changes.
- **Policy:** `AGENTS.md` gains the anchor-maintenance rule.
- **No** change to `src/`, to `app.py`, to runtime behaviour, to the CLI, to config schema,
  or to any `deploy/`, `config/`, or CI path.
- **Intended friction:** from this change on, a PR that shifts lines in `app.py` without
  updating the anchors goes red. That is the point of the issue, and the failure message is
  written to make the fix obvious.
- **Diff coverage:** the new test file is executed by the gate, so the Python lines this
  change adds are covered; the `.md` and `AGENTS.md` edits are not Python and are outside
  diff-cover's scope.
