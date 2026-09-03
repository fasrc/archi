# Tasks — strip break whitespace through inline nodes beside a promoted `<br>`

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
tests, watch them fail for the stated reason, write the smallest fix, run `bash scripts/gate.sh`,
commit. Never end a task with the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** The only production file to edit is `src/data_manager/collectors/processing.py`.
  The only test file to edit is `tests/unit/test_html_to_markdown_processor.py`. Do not touch
  `pyproject.toml`, `requirements/**`, `deploy/**`, `.github/workflows/**`, `config/**`,
  `scripts/gate.sh`, `ralph.conf`, `PROMPT.md`, the `Makefile`, or the `Containerfile`. Do
  not edit `src/data_manager/vectorstore/node_parsing.py`.
- **Coverage.** `processing.py` is inside `--cov=src`, so every new line reports to
  `diff-cover` and must be covered. Both files are black-clean and isort-clean on
  `36fdb420`. Keep them so: run `black` and `isort` on both files before `git add`, so the
  pre-commit writer cannot leave content out of the commit. Check that `git status` is empty
  after each commit.
- **Run `python -m pytest`, not bare `pytest`**, so an editable-install finder cannot resolve
  `src` to a different checkout.
- **The seam.** `_strip_break_whitespace` (`processing.py:301`) is called once, at
  `processing.py:342`, inside the first pass of `_promote_block_code` (`processing.py:318`),
  which runs inside `_worker()` of `_markdownify_deep_safe`. Keep the two passes (strip every
  break, then replace every break) exactly as they are. Do not change `_markdownify_deep_safe`,
  `_fence_language`, `_promoted_fence_language`, `_BR_TRAILING_WS`, or `_BR_LEADING_WS`.
- **bs4 facts to rely on** (bs4 4.12.3, measured 2026-09-03): `tag.find_all(string=True)`
  returns every string descendant in document order, including `Comment` nodes; `Comment` is
  a `NavigableString` subclass, so test `type(node) is NavigableString` to skip it; a `<br>`
  has no string descendants; `Tag` is importable from `bs4`.
- **Known flake.** `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_terminates_running_evaluation_process`
  has raced under CPU load in earlier runs and is unrelated to this change. If the gate
  fails on only that test, re-run it once.

## 1. Resolve the edge text node beside each break

- [ ] 1.1 `model: opus` — Add `_edge_text(br, *, forward: bool, stop_at) -> NavigableString | None`
      to `src/data_manager/collectors/processing.py`, placed directly above
      `_strip_break_whitespace`, change `_strip_break_whitespace` to
      `_strip_break_whitespace(br, *, stop_at) -> None` and route both sides through the new
      helper, and change the one call in `_promote_block_code` to
      `_strip_break_whitespace(br, stop_at=code)`. Docstrings name issue #408 and the shape
      they fix. RED tests first, in `tests/unit/test_html_to_markdown_processor.py`, under a
      new section comment
      `# --- break whitespace through inline nodes beside a promoted <br> (issue #408) ---`
      placed directly after the existing `test_wire_fenced_block_wpautop_newlines_have_no_blank_lines`
      test. No new import is needed: the tests use the module's existing `_promoted_code_text`
      helper, `_promote_block_code`, and `html_to_markdown`. Tests:
      (a) tree-level, through `_promoted_code_text`, exact strings:
        - `test_promote_block_code_drops_newline_inside_inline_tag_after_br`:
          `_promoted_code_text('<p><code>a<br><span>\nb</span></code></p>') == "a\nb"`;
        - `test_promote_block_code_drops_newline_after_inline_tag_that_ends_with_br`:
          `_promoted_code_text('<p><code><span>a<br></span>\nb</code></p>') == "a\nb"`;
        - `test_promote_block_code_keeps_indentation_inside_inline_tag_after_br`:
          `_promoted_code_text('<p><code>a<br><span>\n    b</span></code></p>') == "a\n    b"`;
        - `test_promote_block_code_drops_newline_inside_inline_tag_before_br`:
          `_promoted_code_text('<p><code><span>a\n</span><br>b</code></p>') == "a\nb"`;
        - `test_promote_block_code_drops_newline_before_inline_tag_that_starts_with_br`:
          `_promoted_code_text('<p><code>a\n<span><br>b</span></code></p>') == "a\nb"`;
        - `test_promote_block_code_climbs_through_nested_inline_tags`:
          `_promoted_code_text('<p><code><span><em>a<br></em></span>\nb</code></p>') == "a\nb"`.
      (b) `test_promote_block_code_never_climbs_past_the_code_element` — parse
      `_promote_block_code('<p><code>a<br></code>\nmore</p>')` with
      `BeautifulSoup(result, "html.parser")` and assert `soup.p.contents[-1] == "\nmore"`.
      This passes today and must keep passing; do not contrive a failure for it. It pins
      design D3 (a dropped `stop_at` would strip that text, and the Markdown output would
      not show it).
      (c) exact strings through `html_to_markdown()`, red today:
        - `test_wire_inline_child_newlines_have_no_blank_lines`:
          `'<p><code>a<br><span>\nb</span></code></p>'` → `'```\na\nb\n```'`,
          `'<p><code><span>a<br></span>\nb</code></p>'` → `'```\na\nb\n```'`,
          `'<p><code>a<br><span>\n    b</span></code></p>'` → `'```\na\n    b\n```'`
          (today each returns the same string with a blank line after `a`);
        - `test_wire_comment_inside_inline_tag_is_skipped`:
          `html_to_markdown('<p><code>a<br><span><!-- c -->\nb</span></code></p>') == '```\na\nb\n```'`
          (today: `'```\na\n\nb\n```'`).
      (d) guards that pass today and must keep passing (do not contrive a failure):
        - `test_wire_two_breaks_keep_one_blank_line_through_the_tag_branch`:
          `html_to_markdown('<p><code>a<br><br>b</code></p>') == '```\na\n\nb\n```'` and
          `html_to_markdown('<p><code>a<br />\n<br />\nb</code></p>') == '```\na\n\nb\n```'`.
          After the change the neighbour `<br>` goes through the `Tag` branch of the new
          helper, so this guard pins that the branch contributes no text.
      Run `python -m pytest tests/unit/test_html_to_markdown_processor.py -q` and watch the
      (a) and (c) tests fail on their assertions with a blank line (`"a\n\nb"`,
      `'```\na\n\nb\n```'`) while (b), (d), and every pre-existing test pass.
      Then implement design D1–D5 and D7:
      1. Change `from bs4 import BeautifulSoup, NavigableString` (`processing.py:32`) to
         `from bs4 import BeautifulSoup, NavigableString, Tag`.
      2. `_edge_text(br, *, forward: bool, stop_at) -> NavigableString | None`:
         `node = br`; loop: `sibling = node.next_sibling if forward else node.previous_sibling`;
         `if sibling is not None: break`; `parent = node.parent`;
         `if parent is None or parent is stop_at: return None`; `node = parent`.
         After the loop: `if type(sibling) is NavigableString: return sibling`;
         `if isinstance(sibling, Tag):` collect
         `strings = [s for s in sibling.find_all(string=True) if type(s) is NavigableString]`
         and `if strings: return strings[0] if forward else strings[-1]`; finally `return None`.
         The climb is a loop, not recursion.
      3. `_strip_break_whitespace(br, *, stop_at) -> None`: iterate
         `for forward, pattern in ((False, _BR_TRAILING_WS), (True, _BR_LEADING_WS)):`,
         `node = _edge_text(br, forward=forward, stop_at=stop_at)`; `if node is None: continue`;
         then the existing `stripped = pattern.sub("", str(node))`, the unchanged check, and
         the existing replace-or-extract. The previous side still runs before the next side.
      4. In `_promote_block_code`, `_strip_break_whitespace(br)` becomes
         `_strip_break_whitespace(br, stop_at=code)`. Nothing else in that function changes.
      Update the `_strip_break_whitespace` docstring to say the neighbour is resolved through
      inline nodes (issue #408). Run
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -q` (all green) and
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -k "deeply_nested or recursion" -q`
      (still green: issue #40 is not reintroduced). Gate green; commit.

## 2. Close out

- [ ] 2.1 `model: sonnet` — Verify, push, and open the PR. Steps, in order:
      1. `bash scripts/gate.sh` on the finished branch exits 0. `git status` is empty.
      2. `git diff origin/dev --stat` lists only `src/data_manager/collectors/processing.py`,
         `tests/unit/test_html_to_markdown_processor.py`, and this change's
         `openspec/changes/fix-issue-408-strip-break-whitespace-inline-nodes/` files. Nothing
         under `pyproject.toml`, `requirements/`, or `src/data_manager/vectorstore/`.
      3. Verification: run the script under "Verification" in this change's `design.md`. It
         is offline and needs no extra package. It must print `PASS`.
      4. Push: `git push -u origin fix/issue-408-strip-break-whitespace-inline-nodes`.
      5. Open the PR with
         `gh pr create --repo fasrc/archi --base dev --title "fix(#408): strip break whitespace through inline nodes in the html-to-markdown ingest"`.
         The body MUST contain `Closes #408` on its own line (a closing keyword in the title
         does not link the issue), and MUST contain these sections:
         **What** (one paragraph: the edge text node is resolved through inline tags, the
         climb stops at the `<code>`, the two-pass structure is unchanged);
         **Before / after** (the three issue inputs as a table, from `design.md` D8);
         **Corpus numbers** (from issue #408, 2026-09-02 sample of 60 of 213 KB pages: 25
         multi-line bare `<code>` elements, 0 with a child tag other than `<br>`, 0 of 107
         breaks with a `Tag` neighbour — the fix is defensive and no live page is expected to
         change);
         **Verification** (step-3 result: `PASS`);
         **Guards** (wpautop, `<br><br>`, tab indentation, no-newline whitespace, native
         `<pre>`, inline code, deep nesting — all byte-identical);
         **No re-ingest and no redeploy in this PR**: persisted pages update only on a
         force re-ingest, a separate operator decision;
         **Related**: PR #405 and the Codex thread
         `https://github.com/fasrc/archi/pull/405#discussion_r3912257994` that raised this;
         #406 (PR #414), #407 (PR #415), and #410 edit the same function and test module, and
         PR #414 adds `Tag` to the same import line, so whichever merges later rebases onto
         the earlier one.
         If `gh pr create` against `fasrc/archi` fails with a permissions error, leave the
         branch pushed, do **not** open a PR on any other repository, and stop.
      6. Record the PR URL as a line under this task, tick the task, and commit that edit
         with the gate. Do not merge.
