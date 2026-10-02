# Tasks — keep the link of an anchor that the hoist empties (#430)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
tests, watch them fail for the stated reason, write the smallest fix, run
`bash scripts/gate.sh`, commit. Never end a task with the suite red, and never use
`--no-verify`.

Standing notes for every task:

- **Scope.** The only production file to edit is `src/data_manager/collectors/processing.py`,
  and only the function `_hoist_out_of_inline` (`processing.py:418`). The only test file to
  edit is `tests/unit/test_html_to_markdown_processor.py`. Do not touch `pyproject.toml`,
  `requirements/**`, `deploy/**`, `.github/workflows/**`, `config/**`, `scripts/gate.sh`,
  `ralph.conf`, `PROMPT.md`, the `Makefile`, or the `Containerfile`.
- **Coverage.** `processing.py` is inside `--cov=src`, so every new line reports to
  `diff-cover`. Run `black` and `isort` on both files before `git add`. Check that
  `git status` is empty after each commit.
- **Run `python -m pytest`, not bare `pytest`.**
- **Add the new tests directly after
  `test_hoist_a_em_nested_pre_parent_is_p_two_anchors_each_with_em`** (about line 819), not
  at the end of the file. Use new, unique test names (grep the file first): a reused
  `def test_...` name silently deletes the older test.
- The exact strings to pin are in `specs/ingest-processing/spec.md` of this change and the
  table in design D1. The autolink form `<https://x/y>` is correct (design D1); do not
  change it to `[https://x/y](https://x/y)`.

## 1. Keep the link (test and fix in one task)

- [x] 1.1 Add the tests for every scenario in this change's spec: untitled, titled, blank
      title, nested `<a><em><code>` (exact string and the tree check through
      `_promote_block_code_soup`), whitespace and comment, two blocks, outer `<strong>`, no
      `href` and empty `href`, and the three "other content" strings. Run them with
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -q` and confirm the
      new link tests fail because the output has no link (the unchanged-behaviour tests
      pass). Then, in `_hoist_out_of_inline`, change the end of the loop body to design D1:
      if `_has_content(tail)`, insert `tail` after `pre` as today; otherwise, if
      `parent.name == "a"`, `parent.get("href")` is non-empty, and
      `not _has_content(parent)`, build `link = soup.new_tag("a", href=parent["href"])`, set
      `link.string = (parent.get("title") or "").strip() or parent["href"]`,
      `parent.replace_with(link)`, and `continue`; then keep the existing
      `if not _has_content(parent): parent.decompose()`. Add one sentence about the kept
      link (issue #430) to the docstring. Run the whole file again; all tests pass,
      including the neighbour tests at about lines 806 and 819. Gate green; commit.

## 2. Verify, push, and open the PR

- [ ] 2.1 Confirm `git diff origin/dev --stat` lists only `processing.py`, the test file,
      and files under `openspec/changes/`. Run the gate once more and confirm it exits 0
      with patch coverage at or above 80 %. Confirm `git status` is empty. Push with
      `git push -u origin fix/issue-430-keep-link-on-hoisted-code` (the branch tracks
      `origin/dev`, so `-u` is required). Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, put `closes #430` in the **body** (a
      closing keyword in the title does not link the issue), and say in the body that the
      untitled link renders as the autolink `<https://x/y>` (design D1) and that a relative
      `href` renders as `markdownify` renders it today (design D3). Then stop. Do not merge.
