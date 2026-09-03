# Tasks — hoist a promoted code block out of inline formatting ancestors

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
tests, watch them fail for the stated reason, write the smallest fix, run `bash scripts/gate.sh`,
commit. Never end a task with the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** The only production file to edit is `src/data_manager/collectors/processing.py`.
  The only test file to edit is `tests/unit/test_html_to_markdown_processor.py`. Do not touch
  `pyproject.toml`, `requirements/**`, `deploy/**`, `.github/workflows/**`, `config/**`,
  `scripts/gate.sh`, `ralph.conf`, `PROMPT.md`, the `Makefile`, or the `Containerfile`. Do
  not import `markdown_it` anywhere under `src/` or `tests/`. Do not edit
  `src/data_manager/vectorstore/node_parsing.py`.
- **Coverage.** `processing.py` is inside `--cov=src`, so every new line reports to
  `diff-cover` and must be covered. Both files are black-clean and isort-clean on
  `36fdb420`. Keep them so: run `black` and `isort` on both files before `git add`, so the
  pre-commit writer cannot leave content out of the commit. Check that `git status` is empty
  after each commit.
- **Run `python -m pytest`, not bare `pytest`**, so an editable-install finder cannot resolve
  `src` to a different checkout.
- **The seam.** `_promote_block_code` (`processing.py:318`) wraps the `<code>` with
  `code.wrap(pre)` at `processing.py:349` and runs inside `_worker()` of
  `_markdownify_deep_safe`. The hoist is one call directly after that `wrap` (design D7).
  Do not change `_markdownify_deep_safe`, `_strip_break_whitespace`, `_fence_language`,
  or `_promoted_fence_language`.
- **bs4 facts to rely on** (bs4 4.12.3, measured 2026-09-03): `tag.append(node)` extracts
  `node` from its current parent first; `parent.insert_after(pre)` extracts `pre` from
  `parent`; `soup.new_tag(name, attrs=dict(parent.attrs))` copies a list-valued `class` and
  serializes it back to `class="x y"`; a `Comment` is a `NavigableString` subclass, so test
  `type(node) is NavigableString` to skip it. `Tag` is importable from `bs4`.
- **Known flake.** `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_terminates_running_evaluation_process`
  has raced under CPU load in earlier runs and is unrelated to this change. If the gate
  fails on only that test, re-run it once.

## 1. Split the marked inline ancestors around the promoted block

- [x] 1.1 `model: opus` — Add `_INLINE_MARKUP_TAGS` and `_hoist_out_of_inline(pre, soup) -> None`
      to `src/data_manager/collectors/processing.py`, placed directly above
      `_promote_block_code`, and call the helper once directly after `code.wrap(pre)`.
      Docstrings name issue #406 and the shape they fix. RED tests first, in
      `tests/unit/test_html_to_markdown_processor.py`, under a new section comment
      `# --- hoist a promoted block out of inline formatting ancestors (issue #406) ---`.
      Import `_INLINE_MARKUP_TAGS` by name in the module's existing
      `from src.data_manager.collectors.processing import (...)` block, so the whole module
      goes red on an `ImportError` first. Add a module-level test helper
      `_assert_one_clean_fence(md)` that asserts: exactly two lines of `md` start with
      ```` ``` ````, the second of them is exactly ```` ``` ````, and no line starts with
      `# `. Tests:
      (a) `test_inline_markup_tags_set` — `_INLINE_MARKUP_TAGS == frozenset({"a", "b",
      "strong", "em", "i", "del", "s", "kbd", "samp", "sub", "sup"})`.
      (b) tree tests through `_promote_block_code`, each parsing the result with
      `BeautifulSoup(result, "html.parser")` and asserting on the tree, not the string:
        - `'<p><em><code>a<br># heading</code></em></p>'` — the `<pre>` element's parent is
          named `p`, `soup.find("em") is None`, and the `<pre>` has `_PROMOTED_ATTR`;
        - `'<p><strong>Note: <code>a<br>b</code> done</strong></p>'` — the `<p>` element's
          `Tag` children are, in order, `strong`, `pre`, `strong`, and the two `strong`
          elements' `get_text().strip()` are `"Note:"` and `"done"` (use `.strip()` here —
          task 1.2 trims the edge whitespace, and this test must stay green through it);
        - `'<p><a href="http://x" title="T">See <code>a<br>b</code> now</a></p>'` — exactly
          two `<a>` elements, both with `href == "http://x"` and `title == "T"`, and the
          `<pre>` sits between them;
        - `'<p><a href="http://x"><em>See <code>a<br>b</code> now</em></a></p>'` — the `<pre>`
          element's parent is named `p`, and each of the two `<a>` elements contains one `<em>`;
        - `'<p><em class="note">x <code>a<br>b</code> y</em></p>'` — both `<em>` elements
          have `class == ["note"]`;
        - `'<p><em> <code>a<br>b</code> </em></p>'` and
          `'<p><em><!-- c --><code>a<br>b</code></em></p>'` — `soup.find("em") is None`;
        - `'<p><a href="http://x"><img src="i.png"/><code>a<br>b</code></a></p>'` — exactly
          one `<a>`, it contains the `<img>`, and its `find_next_sibling()` is the `<pre>`;
        - `'<p><em><span><code>a<br>b</code></span></em></p>'` — the `<pre>` element's parent
          is named `span` (the loop stops at a tag outside the set; design Non-Goals).
      (c) exact strings through `html_to_markdown()`, red today:
        - `'<p><em><code>a<br># heading</code></em></p>'` → `'```\na\n# heading\n```'`
          (today: `'*```\na\n# heading\n```*'`);
        - `'<p><strong><code class="bash">a<br># heading</code></strong></p>'` →
          `'```bash\na\n# heading\n```'` (today: `'**```bash\na\n# heading\n```**'`);
        - `'<p><kbd><code>a<br># heading</code></kbd></p>'` → `'```\na\n# heading\n```'`
          (today: `` '```` ```\na\n# heading\n``` ````' ``).
      (d) structural checks through `html_to_markdown()` with `_assert_one_clean_fence` on
      the four issue inputs (the two above, `'<p><a href="http://x">See <code>a<br>b</code> now</a></p>'`,
      and `'<p><strong>Note: <code>a<br>b</code> done</strong></p>'`), plus `"[See](http://x)"`
      and `"[now](http://x)"` `in` the link output and `"**Note:**"` and `"**done**"` `in` the
      split output. Red today: the em and strong rows have no line that starts with
      ```` ``` ````, the link row lacks `[See](http://x)`, the split row lacks `**Note:**`.
      Do not assert exact strings for the link and split rows in this task; that is task 1.2.
      (e) guards that pass today and must keep passing (do not contrive a failure):
      `html_to_markdown('<p><em>Add <code>--gpus=1</code>.</em></p>') == '*Add `--gpus=1`.*'`
      and `html_to_markdown('<p>Before <code class="bash">a<br>b</code> after</p>') == 'Before\n\n```bash\na\nb\n```\n\nafter'`.
      Watch (a)–(d) fail — first on the import, then, after a stub import, on the assertions.
      Then implement design D2–D5 and D7, **without** the edge trim of D6:
      `_INLINE_MARKUP_TAGS` as the frozenset above; a private `_has_content(tag) -> bool`
      that is true when any direct child is a `Tag` or is an exact `NavigableString` with
      non-whitespace text; and `_hoist_out_of_inline(pre, soup)`:
      `while isinstance(pre.parent, Tag) and pre.parent.name in _INLINE_MARKUP_TAGS:` —
      `parent = pre.parent`; `tail = soup.new_tag(parent.name, attrs=dict(parent.attrs))`;
      `for node in list(pre.next_siblings): tail.append(node)`; `parent.insert_after(pre)`;
      `if _has_content(tail): pre.insert_after(tail)`; `if not _has_content(parent): parent.decompose()`.
      Add `Tag` to the existing `from bs4 import BeautifulSoup, NavigableString` line. In
      `_promote_block_code`, add `_hoist_out_of_inline(pre, soup)` directly after
      `code.wrap(pre)`. Run `python -m pytest tests/unit/test_html_to_markdown_processor.py -q`
      (all green) and
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -k "deeply_nested or recursion" -q`
      (still green). Note for 1.2: at this point
      `html_to_markdown('<p><strong>Note: <code>a<br>b</code> done</strong></p>')` returns
      `'**Note:** \n\n```\na\nb\n```\n\n **done**'` with a space before the first `\n\n` and
      after the last one. That is expected here and is the next task's red. Gate green;
      commit.

- [ ] 1.2 `model: opus` — Drop the whitespace at the cut (design D6). RED tests first, exact
      strings through `html_to_markdown()`:
        - `'<p><a href="http://x">See <code>a<br>b</code> now</a></p>'` →
          `'[See](http://x)\n\n```\na\nb\n```\n\n[now](http://x)'`;
        - `'<p><strong>Note: <code>a<br>b</code> done</strong></p>'` →
          `'**Note:**\n\n```\na\nb\n```\n\n**done**'`;
        - `'<p><a href="http://x"><em>See <code>a<br>b</code> now</em></a></p>'` →
          `'[*See*](http://x)\n\n```\na\nb\n```\n\n[*now*](http://x)'`;
        - `'<p><a href="http://x" title="T">See <code>a<br>b</code> now</a></p>'` →
          `'[See](http://x "T")\n\n```\na\nb\n```\n\n[now](http://x "T")'`;
        - `'<p><em>x <code>a<br>b</code> y <code>c<br>d</code> z</em></p>'` →
          `'*x*\n\n```\na\nb\n```\n\n*y*\n\n```\nc\nd\n```\n\n*z*'`;
        - `'<p><em>x <img src="i.png"/> <code>a<br>b</code></em></p>'` →
          `'*x ![](i.png)*\n\n```\na\nb\n```'` (the space after `x` does not touch the cut
          and is kept);
        - and one tree test: in `_promote_block_code('<p><strong>Note: <code>a<br>b</code> done</strong></p>')`
          the two `<strong>` elements' `.string` are exactly `"Note:"` and `"done"`.
      Watch them fail: after task 1.1 each output carries a space before the first `\n\n`
      and after the last `\n\n` (for example `'**Note:** \n\n```\na\nb\n```\n\n **done**'`),
      and the `.string` values are `"Note: "` and `" done"`. Then implement D6 as a private
      helper `_trim_cut_whitespace(half, *, trailing: bool) -> None` next to
      `_hoist_out_of_inline`: collect `[n for n in half.descendants if type(n) is NavigableString]`;
      if empty, return; take the last node when `trailing` else the first; `rstrip()` it when
      `trailing` else `lstrip()`; if unchanged, return; if the stripped text is non-empty,
      `node.replace_with(stripped)`, else `node.extract()`. Call
      `_trim_cut_whitespace(parent, trailing=True)` and `_trim_cut_whitespace(tail, trailing=False)`
      inside the loop directly after `parent.insert_after(pre)` and before the two
      `_has_content` checks. Re-run the whole test module (all green, including every task
      1.1 test and the pre-existing #399 tests) and the `-k "deeply_nested or recursion"`
      selection. Gate green; commit.

## 2. Close out

- [ ] 2.1 `model: sonnet` — Verify, push, and open the PR. Steps, in order:
      1. `bash scripts/gate.sh` on the finished branch exits 0. `git status` is empty.
      2. `git diff origin/dev --stat` lists only `src/data_manager/collectors/processing.py`,
         `tests/unit/test_html_to_markdown_processor.py`, and this change's
         `openspec/changes/fix-issue-406-hoist-promoted-code-out-of-inline/` files. Nothing
         under `pyproject.toml`, `requirements/`, or `src/data_manager/vectorstore/`.
      3. Verification, best effort: run the first script under "Verification" in this
         change's `design.md`. It needs the `markdown_it` package
         (`pip install 'markdown-it-py==4.2.0'` if absent). It must print `PASS`. If the
         package cannot be installed, do not fail the task; write the reason in the PR body
         and cite the 2026-09-03 prototype table in `design.md`.
      4. Corpus sample, best effort: run the second script under "Verification" in
         `design.md` (network, about 1 minute). Put its output line in the PR body. If the
         network is unavailable, say so and cite the 2026-09-02 baseline
         (`multi-line bare code=25 under marked inline ancestor=0`).
      5. Push: `git push -u origin fix/issue-406-hoist-promoted-code-out-of-inline`.
      6. Open the PR with
         `gh pr create --repo fasrc/archi --base dev --title "fix(#406): hoist a promoted code block out of inline formatting ancestors in the html-to-markdown ingest"`.
         The body MUST contain `Closes #406` on its own line (a closing keyword in the title
         does not link the issue), and MUST contain these sections:
         **What** (one paragraph: split, not unwrap; the eleven-tag set; edge whitespace);
         **Before / after** (the four issue inputs as a table, from `design.md` D8);
         **Corpus numbers** (the step-4 line, or the cited baseline — the shape is rare and
         the fix is defensive);
         **Verification** (step-3 result: `PASS`, or the reason it did not run);
         **Guards** (inline code inside emphasis, native `<pre>`, wpautop, prose around a
         block, deep nesting — all byte-identical);
         **No re-ingest and no redeploy in this PR**: persisted pages update only on a
         force re-ingest, a separate operator decision;
         **Related**: PR #405 and the Codex thread
         `https://github.com/fasrc/archi/pull/405#discussion_r3911513054` that raised this;
         #407, #408, and #410 edit the same function and test module, so whichever merges
         later rebases onto the earlier one.
         If `gh pr create` against `fasrc/archi` fails with a permissions error, leave the
         branch pushed, do **not** open a PR on any other repository, and stop.
      7. Record the PR URL as a line under this task, tick the task, and commit that edit
         with the gate. Do not merge.
