# Tasks — relative and fragment self-links as Markdown links (#604)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
tests, watch them fail for the stated reason, write the smallest fix, run
`bash scripts/gate.sh`, commit. Never end a task with the suite red, and never use
`--no-verify`.

Standing notes for every task:

- **Scope.** The only production file to edit is `src/data_manager/collectors/processing.py`.
  The only test file to edit is `tests/unit/test_html_to_markdown_processor.py`. The only doc
  to edit is `docs/docs/configuration.md`. Do not touch `pyproject.toml`, `requirements/**`,
  `deploy/**`, `.github/workflows/**`, `config/**`, `scripts/gate.sh`, `ralph.conf`,
  `PROMPT.md`, the `Makefile`, or the `Containerfile`.
- **Coverage.** `processing.py` is inside `--cov=src`, so every new line reports to
  `diff-cover`. Run `black` and `isort` on the changed Python files before `git add`. Check
  that `git status` is empty after each commit.
- **Run `python -m pytest`, not bare `pytest`.**
- **Add the new tests directly after
  `test_hoist_anchor_with_relative_href_keeps_a_markdown_link`** (about line 980), not at the
  end of the file. Use new, unique test names (grep the file first): a reused `def test_...`
  name silently deletes the older test.
- The exact strings to pin are in `specs/ingest-processing/spec.md` of this change and the
  table in design D1. Absolute URLs stay `<...>` autolinks; do not change them.

## 1. Rewrite every scheme-less self-link (test and fix in one task)

- [x] 1.1 Add one test for every scenario in this change's spec (exact strings). Run
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -q` and confirm the
      `/docs`, `#sec`, `docs/page.html`, whitespace, `/a_b`, and kept-link `/a_b` tests fail
      because the output is `<href>` or keeps the unescaped text (the unchanged-behaviour
      tests pass). Then change `_ArchiMarkdownConverter.convert_a` to design D1 (import
      `chomp` from `markdownify` in the existing import line, keep the `_noformat` guard of
      design D3), and remove `_KEPT_LINK_ATTR` per design D2 (constant, its comment, and the
      `attrs=` argument in `_hoist_out_of_inline`; `grep -rn _KEPT_LINK_ATTR src tests` must
      print nothing). Update the class docstring (`convert_a` now covers every self-link
      without a URI scheme, issues #430 and #604) and the method docstring. Run the whole
      file again; all tests pass, including
      `test_hoist_anchor_with_relative_href_keeps_a_markdown_link`. If any older test pinned
      a `</...>` or `<#...>` output, update it and name it in the commit message so task 3.1
      can list it in the PR body. Gate green; commit.

## 2. Document the change

- [x] 2.1 In `docs/docs/configuration.md`, in the conversion list that contains "A promoted
      block leaves its inline ancestors" (about line 780), add one bullet after the
      "Content after a nested list starts on its own line" bullet: a link whose text equals a
      relative or fragment `href`, such as `/docs`, becomes `[/docs](/docs)`, because a
      CommonMark autolink needs a URI scheme and `</docs>` reads as an HTML end tag (issue
      #604); absolute URLs stay `<https://...>`; like the other items, it reaches disk only for
      new or force-overwritten documents. In the "A promoted block" bullet, keep the sentence
      about `[/docs](/docs)`. Gate green; commit.

## 3. Verify, push, and open the PR

- [x] 3.1 Confirm `git diff origin/dev --stat` lists only `processing.py`, the test file,
      `docs/docs/configuration.md`, and files under
      `openspec/changes/fix-issue-604-relative-autolink/`. Run the gate once more and confirm
      it exits 0 with patch coverage at or above 80 %. Confirm `git status` is empty. Push
      with `git push -u origin fix/issue-604-relative-autolink` (the branch tracks
      `origin/dev`, so `-u` is required). Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, put `closes #604` in the **body** (a
      closing keyword in the title does not link the issue), list any older test whose pinned
      output changed, note the kept-link `/a_b` output change (design D2), and say that
      issue #605 must rebase on this PR and extend the same `convert_a`. Then stop. Do not
      merge.
