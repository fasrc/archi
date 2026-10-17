## 1. Tags that write link text with no text in them (red tests and fix in one task)

- [ ] 1.1 In `tests/unit/test_html_to_markdown_processor.py`, add exact-string tests for
      every scenario of the requirement "A hoisted anchor keeps one link when a tag
      writes link text with no text in it" in
      `openspec/changes/fix-issue-713-hoist-q-and-source-marker/specs/ingest-processing/spec.md`.
      Use `pytest.mark.parametrize` for the heading (`h1`–`h6`, expected `#` × level)
      and the cell (`td`, `th`) cases. Insert the new tests right AFTER
      `test_hoist_two_blocks_then_text_give_no_extra_link` and BEFORE
      `test_link_destination_unmatched_close_paren_escapes` — NOT after the last line of
      the file. Use new, unique test names (grep each name first; a duplicate `def`
      silently deletes the older test). Run
      `python -m pytest -q tests/unit/test_html_to_markdown_processor.py` and confirm
      the new tests FAIL, except the two guards (empty span, lone block), which pass
      before and after. Then, in the same task, make design D1 in
      `src/data_manager/collectors/processing.py`: add `q`, `h1`–`h6`, `td`, `th`, `tr`
      to `_RENDERS_WITHOUT_TEXT`, update its comment, and add issue #713 to the
      `_renders_link_text` docstring. Re-run the test file and confirm ALL tests pass;
      no existing test output is expected to change (if one does, STOP and write it
      under "## Measurement" in `design.md`). Run `bash scripts/gate.sh` and commit
      only when it is green (message: `count empty q, heading, and cell tags as
      hoisted link text`).

## 2. Source marker attributes (red tests and fix in one task)

- [ ] 2.1 In the same test file, add exact-string tests for every scenario of the
      requirement "The hoist ignores its marker attributes in the source HTML", right
      AFTER the tests of task 1.1 and BEFORE
      `test_link_destination_unmatched_close_paren_escapes`. Use new, unique test
      names. `_promote_block_code` is already imported in the test file. Run the test
      file and confirm the new tests FAIL, except the guard "A promoted block still
      carries the marker", which passes before and after. Then, in the same task, add
      `_HOIST_MARKER_ATTRS` and `_drop_source_markers` (design D2) directly above
      `_promote_block_code`, call `_drop_source_markers(soup)` right after the
      `BeautifulSoup(html, "html.parser")` line in `_promote_block_code`, and add one
      sentence about it to that function's docstring. Re-run the test file and confirm
      ALL tests pass. Run `bash scripts/gate.sh` and commit only when it is green
      (message: `ignore hoist marker attributes in source html`).

## 3. Docs

- [ ] 3.1 In `docs/docs/configuration.md`, make the two edits of design D3 in the bullet
      "A promoted block leaves its inline ancestors." and change nothing else. Run
      `bash scripts/gate.sh` and commit when green (message: `document the hoist link
      text tags and source markers`).

## 4. Publish

- [ ] 4.1 Push the branch:
      `git push -u origin fix/issue-713-hoist-q-and-source-marker`. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-713-hoist-q-and-source-marker`
      with the title `fix(ingest): one link for a hoisted anchor with an empty q, and
      ignore source hoist markers` and `Closes #713` in the BODY (not the title). Pass
      the body with `--body` or a file under `/tmp`; do NOT commit a PR-body file to the
      repository. In the body, copy the D1 table and the "## Measurement" section of
      `design.md`, list any pinned test output that changed (expected: none), and add
      one line: "The two Codex threads on #602 (r4171300227, r4171300228) still need a
      reply that names the fix commits." STOP at the open PR — do not merge.
