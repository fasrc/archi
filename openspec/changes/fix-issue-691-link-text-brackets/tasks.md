## 1. Unbalanced brackets in link text (measure, red tests, and fix in one task)

- [ ] 1.1 Measure first. Run the converter over the HTML test fixtures in `tests/` (and
      any local KB HTML you can read without network or secrets; skip it if there is
      none) and count `<a>` elements whose rendered link text has an unbalanced `[` or
      `]` outside a code span. Write the sample, the link count, and the unbalanced
      count under "## Measurement" in
      `openspec/changes/fix-issue-691-link-text-brackets/design.md`. Then, in
      `tests/unit/test_html_to_markdown_processor.py`, add exact-string tests for every
      scenario in `specs/ingest-processing/spec.md`, plus direct tests for
      `_escape_unbalanced_brackets` (`"] ["` → `"\\] \\["`, a balanced string returned
      unchanged, an escaped `\\]` unchanged, a code span `` `a]b` `` unchanged, an
      unmatched backtick run followed by `]` → the `]` escaped, a trailing lone `\\`,
      the empty string). Insert the new tests right AFTER
      `test_link_label_helper_escapes_entity_lead_only` and BEFORE
      `test_renders_link_text_stops_at_first_text` — NOT after the last line of the
      file. Use new, unique test names (grep each name first). Import the helper next to
      `_link_destination` in the test file's import list. Run
      `python -m pytest -q tests/unit/test_html_to_markdown_processor.py` and confirm
      the new non-guard tests FAIL. Then, in the same task, add
      `_escape_unbalanced_brackets` next to `_escape_link_label` in
      `src/data_manager/collectors/processing.py` (design D1) and apply it in
      `_ArchiMarkdownConverter.convert_a` (design D2). Add issue #691 to the method
      docstring. Re-run the test file and confirm ALL tests pass; no existing test
      output is expected to change (if one does, record it under "## Measurement").
      Run `bash scripts/gate.sh` and commit only when it is green (message:
      `escape unbalanced brackets in link text`).

## 2. Docs

- [ ] 2.1 In `docs/docs/configuration.md`, add the bullet of design D3 after the
      issue #605 bullet ("A link target that Markdown would misread is escaped.").
      Run `bash scripts/gate.sh` and commit when green (message:
      `document link text bracket escaping`).

## 3. Publish

- [ ] 3.1 Push the branch: `git push -u origin fix/issue-691-link-text-brackets`.
      Open the PR: `gh pr create --repo fasrc/archi --base dev` with `Closes #691` in
      the BODY (not the title). In the body, copy the measurement from design.md
      "## Measurement" and list any pinned test output that changed (expected: none).
      STOP at the open PR — do not merge.
