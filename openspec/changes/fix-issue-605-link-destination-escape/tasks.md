## 1. Link destinations round-trip (red tests and fix in one task)

- [x] 1.1 In `tests/unit/test_html_to_markdown_processor.py`, add exact-string tests next
      to the link tests (after `test_hoist_two_blocks_then_text_give_no_extra_link`, NOT
      after the last line of the file), one test per scenario in
      `specs/ingest-processing/spec.md`: unmatched `)`, unmatched `(`, a space, `<` and
      `>` in an angle-bracket destination, a title kept, an autolink with a space, the
      kept link around hoisted code, and the two guards (`/w/Foo_(bar)`, `https://x/y`).
      Add direct tests for `_link_destination` (bare valid, space, tab, unbalanced, a
      backslash-escaped `\)` that stays bare, empty string stays bare). Use new, unique
      test names. Run `python -m pytest -q tests/unit/test_html_to_markdown_processor.py`
      and confirm the new non-guard tests FAIL. Then, in the same task, add
      `_link_destination` next to `_URI_SCHEME` in
      `src/data_manager/collectors/processing.py` (design D1) and apply design D2, D3
      and D4 at the end of `_ArchiMarkdownConverter.convert_a`. Update the method
      docstring to name issue #605. Re-run the test file and confirm ALL tests pass. No
      existing test output is expected to change; if one does, record it for the PR
      body. Run `bash scripts/gate.sh` and commit only when it is green (message:
      `escape link destinations with a space or unbalanced parens`).

## 2. Docs

- [x] 2.1 In `docs/docs/configuration.md`, add one bullet after the "A promoted block
      leaves its inline ancestors." bullet: a link whose target has a space or an
      unbalanced parenthesis is written as `[text](<target>)` so the target survives
      (issue #605); other links convert as before; like every item in this list, it
      reaches disk only for new or force-overwritten documents. Run
      `bash scripts/gate.sh` and commit when green (message: `document link destination
      escaping`).

## 3. Publish

- [ ] 3.1 Push the branch: `git push -u origin fix/issue-605-link-destination-escape`.
      Open the PR: `gh pr create --repo fasrc/archi --base dev` with `Closes #605` in
      the BODY (not the title). In the body, say that open PR #664 (issue #604) also
      edits `convert_a` and that the second PR to merge must rebase; list any pinned
      test output that changed (expected: none). STOP at the open PR — do not merge.
