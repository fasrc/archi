# Tasks — keep a newline after a nested list in the HTML-to-Markdown ingest

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail for the stated reason, write the smallest fix, run `bash scripts/gate.sh`,
commit. Never end a task with the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** The only production file to edit is `src/data_manager/collectors/processing.py`.
  The only test file to edit is `tests/unit/test_html_to_markdown_processor.py`. Task 2.1 also
  edits `docs/docs/configuration.md`. Do not touch `src/data_manager/vectorstore/node_parsing.py`,
  `pyproject.toml`, `requirements/**`, `deploy/**`, `.github/workflows/**`, `config/**`,
  `scripts/gate.sh`, `ralph.conf`, `PROMPT.md`, the `Makefile`, or the `Containerfile`. Do
  not bump `markdownify`.
- **Coverage.** `processing.py` is inside `--cov=src`, so every new line reports to
  `diff-cover` and must be covered. Both files are black 24.10.0 and isort 6.0.1 clean today.
  Keep them so, and run `black` and `isort` on both files before `git add`, so the pre-commit
  writer cannot leave content out of the commit. Check that `git status` is empty after each
  commit.
- **Run `python -m pytest`, not bare `pytest`**, so an editable-install finder cannot resolve
  `src` to a different checkout.
- **Append, do not insert.** New tests go at the END of the test file under a comment banner
  that names issue #410. The file's current last line,
  `    assert _promoted_code_text("<p><code>a\tb\t<br>c</code></p>") == "a\tb\t\nc"`, must
  appear unchanged as trailing context in `git diff origin/dev -- tests/unit/test_html_to_markdown_processor.py`.
  Nightly runs have inserted new tests above the final line and swallowed the previous
  test's last assertion.
- **The seam.** `_markdownify_deep_safe` (`processing.py:397`) runs the conversion in its
  inner `_worker()`, whose call reads `markdownify(...)`. That call site is NOT edited. The
  module keeps a function named `markdownify` (design D5) because two existing tests
  monkeypatch `src.data_manager.collectors.processing.markdownify`; those tests must pass
  unchanged.
- **The dispatch trap.** `markdownify` binds `convert_ul` and `convert_ol` to the base
  `convert_list` at class-definition time. A subclass that overrides `convert_list` alone is
  never called (design D2). The subclass must contain `convert_ul = convert_list` and
  `convert_ol = convert_list` after the method definition.
- **Known flake.** `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_terminates_running_evaluation_process`
  has raced under CPU load in earlier runs and is unrelated to this change. If the gate
  fails on only that test, re-run it once.

## 1. The helpers and the converter

- [x] 1.1 `model: opus` — Add the follower helpers to
      `src/data_manager/collectors/processing.py`, placed directly above
      `_markdownify_deep_safe` and below `_promoted_fence_language`:
      `_SELF_SEPARATING_FOLLOWERS` (a `frozenset` of exactly `article`, `blockquote`, `br`,
      `div`, `dl`, `dt`, `figcaption`, `h1`, `h2`, `h3`, `h4`, `h5`, `h6`, `hr`, `ol`, `p`,
      `pre`, `section`, `table`), `_next_content_sibling(el)`, and
      `_nested_list_needs_break(el, text) -> bool`, each with a docstring that names issue
      #410. RED tests first, appended to `tests/unit/test_html_to_markdown_processor.py`
      under a `# --- issue #410: newline after a nested list ---` banner. Import the three
      names in the module's existing `from src.data_manager.collectors.processing import (...)`
      block, so the red is an `ImportError`. Build elements with
      `BeautifulSoup(html, "html.parser")` (add `from bs4 import BeautifulSoup, Comment` to
      the test imports) and assert:
      (a) `_SELF_SEPARATING_FOLLOWERS` equals the nineteen-name set above;
      (b) for `<li>Outer<ul><li>Inner</li></ul>   <!-- c -->tail</li>`,
      `_next_content_sibling(li.ul)` is the text node `tail` (whitespace and the comment are
      skipped);
      (c) for `<li>Outer<ul><li>Inner</li></ul><p>Para</p></li>`,
      `_next_content_sibling(li.ul)` is the `<p>` tag;
      (d) for `<li>Outer<ul><li>Inner</li></ul>  </li>` and for
      `<li>Outer<ul><li>Inner</li></ul><!-- only --></li>`, `_next_content_sibling(li.ul)`
      is `None`;
      (e) `_nested_list_needs_break(ul, "Inner")` is `True` when the follower is the text
      `tail`, the inline `<a href="http://x">link</a>`, the inline `<code>x</code>`, or a
      sibling `<li>Configure a bundle</li>`;
      (f) `_nested_list_needs_break(ul, "Inner")` is `False` when the follower is
      `<p>Para</p>`, `<pre>code</pre>`, `<h3>Head</h3>`, `<ul><li>Second</li></ul>`, or
      `<br>`, when there is no follower, and when the follower is a comment only;
      (g) `_nested_list_needs_break(ul, "   ")` is `False` even with the text follower
      `tail` (an empty nested list adds nothing, design D4).
      Watch every new test fail on the import. Then implement per design D3 and D4: import
      `Comment, Doctype, Tag` from `bs4` on the existing `from bs4 import ...` line;
      `_next_content_sibling` walks `el.next_sibling` and returns the first `Tag`, or the
      first `NavigableString` that is not a `Comment` or `Doctype` and has non-blank text,
      else `None`; `_nested_list_needs_break` returns `False` when `text.strip()` is empty or
      there is no follower, and otherwise returns whether the follower is NOT a `Tag` whose
      `name` is in `_SELF_SEPARATING_FOLLOWERS`. Gate green; commit.
- [x] 1.2 `model: opus` — Add `_ArchiMarkdownConverter(MarkdownConverter)` and the wrapper
      `markdownify(html: str, **options) -> str` to `processing.py`, directly below the
      helpers from 1.1, and change the import line `from markdownify import markdownify` to
      `from markdownify import MarkdownConverter`. RED tests first, appended after the 1.1
      tests, all through `html_to_markdown()` unless stated:
      (a) Snippet A: `html_to_markdown('<ul><li>Outer item<ul><li>Inner: <pre>x = 1</pre></li></ul>After the nested list.</li></ul>')`
      equals ``'* Outer item\n  + Inner:\n\n    ```\n    x = 1\n    ```\n  After the nested list.'``
      exactly, its lines include one that is exactly ``'    ```'``, and the substring
      ```` ```After ```` does not occur. Today it returns
      ``'* Outer item\n  + Inner:\n\n    ```\n    x = 1\n    ```After the nested list.'``, so
      this fails on the assertion;
      (b) Snippet C: `html_to_markdown('<ul><li>Outer item<ul><li>Inner ends in prose</li></ul>After the nested list.</li></ul>')`
      equals `'* Outer item\n  + Inner ends in prose\n  After the nested list.'` and
      `proseAfter` does not occur. Fails today;
      (c) Snippet D: `html_to_markdown('<ul><li>A<ul><li><pre>docker rm alpine</pre></li></ul><li>Configure a bundle</li></li></ul>')`
      equals ``'* A\n  + ```\n    docker rm alpine\n    ```\n  * Configure a bundle'`` and
      the substring ```` ```* ```` does not occur. Fails today;
      (d) ordered: `html_to_markdown('<ol><li>Outer<ol><li>Inner</li></ol>After.</li></ol>')`
      equals `'1. Outer\n   1. Inner\n   After.'`. Fails today (`'1. Outer\n   1. InnerAfter.'`);
      (e) inline follower: `html_to_markdown('<ul><li>Outer<ul><li>Inner</li></ul><a href="http://x">link</a> tail</li></ul>')`
      equals `'* Outer\n  + Inner\n  [link](http://x) tail'`. Fails today;
      (f) comment then text: `html_to_markdown('<ul><li>Outer<ul><li>Inner</li></ul><!-- c -->tail</li></ul>')`
      equals `'* Outer\n  + Inner\n  tail'`. Fails today (`'* Outer\n  + Innertail'`);
      (g) dispatch: `_ArchiMarkdownConverter.convert_ul is _ArchiMarkdownConverter.convert_list`
      and `_ArchiMarkdownConverter.convert_ol is _ArchiMarkdownConverter.convert_list`
      (import the class by name; fails on the import today);
      (h) processor: `HtmlToMarkdownProcessor().process(_html_resource(content=<the (a) HTML>))`
      has suffix `md`, its `get_content()` equals `html_to_markdown(<the (a) HTML>)`, and
      does not contain ```` ```After ````. Fails today on the substring assertion;
      (i) guards, exact strings, all pass today and must keep passing — do not contrive a
      failure for them:
      `'<ul><li>Outer<ul><li>Inner</li></ul></li><li>Next outer</li></ul>'` →
      `'* Outer\n  + Inner\n* Next outer'`;
      `'<ul><li>Outer<ul><li>Inner</li></ul>\n  </li><li>Next outer</li></ul>'` → the same
      string;
      `'<ul><li>Outer<ul><li>Inner</li></ul><p>Para</p></li></ul>'` → `'* Outer\n  + Inner\n\n  Para'`;
      `'<ul><li>Outer<ul><li>Inner</li></ul><pre>code</pre></li></ul>'` →
      ``'* Outer\n  + Inner\n\n  ```\n  code\n  ```'``;
      `'<ul><li>Outer<ul><li>Inner</li></ul><h3>Head</h3></li></ul>'` → `'* Outer\n  + Inner\n\n  ### Head'`;
      `'<ul><li>Outer<ul><li>Inner</li></ul><ul><li>Second</li></ul></li></ul>'` → `'* Outer\n  + Inner\n  + Second'`;
      `'<ul><li>Outer<ul><li>Inner</li></ul><br>tail</li></ul>'` → `'* Outer\n  + Inner  \n  tail'`;
      `'<ul><li>Outer<ul></ul>tail</li></ul>'` → `'* Outer\n  tail'`;
      `'<ul><li>Outer<ul><li>Inner</li></ul><!-- c --></li></ul>'` → `'* Outer\n  + Inner'`;
      `'<ul><li>a</li></ul>tail text'` → `'* a\n\ntail text'`.
      Watch (a) through (h) fail. Then implement per design D2 and D5: the class overrides
      `convert_list(self, el, text, parent_tags)` as `out = super().convert_list(el, text, parent_tags)`,
      returning `out + "\n"` when `"li" in parent_tags and _nested_list_needs_break(el, text)`
      and `out` otherwise, followed by the two class attributes `convert_ul = convert_list`
      and `convert_ol = convert_list`; the wrapper is
      `return _ArchiMarkdownConverter(**options).convert(html)` with a docstring that names
      issue #410 and says why the name is kept. Do not edit `_markdownify_deep_safe`. Run
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -q` (all green,
      including `test_converter_raises_keeps_original` and `test_blank_output_keeps_original`
      unchanged) and
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -k "deeply_nested or recursion" -q`
      (still green: issue #40 is not reintroduced). Gate green; commit.

## 2. Docs

- [x] 2.1 `model: sonnet` — Add one bullet to `docs/docs/configuration.md` in the
      "Behavior and caveats" list of the Processing section, directly after the bullet that
      starts `**Multi-line code becomes a fenced block.**` and before the bullet that starts
      `**Cost.**`. Bold lead: `**Content after a nested list starts on its own line.**`. Body,
      in the neighbours' voice and wrapped like them: `markdownify` drops the newline after a
      list nested inside a list item, so the text, inline element, or sibling item that
      followed it was glued onto the nested list's last line — onto a closing code fence when
      the last nested item ends in a code block (issue #410); the conversion puts that
      newline back when the follower does not start a new line by itself, and a following
      paragraph, code block, heading, or list is unchanged; like the body slice, the change
      reaches disk only for new or force-overwritten documents — see *Applying to an
      existing corpus* below. No code change in this task. Run
      `python -m pytest tests/unit/test_python_version_declaration.py -q` (it reads every
      docs page) and the gate. Gate green; commit.

## 3. Close out

- [x] 3.1 `model: sonnet` — Verify, push, and open the PR. Steps, in order:
      1. `bash scripts/gate.sh` on the finished branch exits 0. `git status` is empty.
      2. `git diff origin/dev --stat` lists only `src/data_manager/collectors/processing.py`,
         `tests/unit/test_html_to_markdown_processor.py`, `docs/docs/configuration.md`, and
         this change's `openspec/changes/fix-issue-410-nested-list-newline/` files.
         `git diff origin/dev -- src/data_manager/vectorstore/node_parsing.py` prints nothing.
         Nothing under `pyproject.toml` or `requirements/`.
      3. Live verification, best effort: run the script under "Live verification" in this
         change's `design.md`. It needs network access to `slurm.schedmd.com`. Expect
         `glued closers on the live page: 0`. If the network is unavailable, do not fail the
         task; write the reason in the PR body and use the 2026-09-04 baseline and prototype
         numbers from `design.md` (3 → 0 glued closers; 36,948 → 36,966 characters).
      4. Push: `git push -u origin fix/issue-410-nested-list-newline`.
      5. Open the PR with
         `gh pr create --repo fasrc/archi --base dev --title "fix(#410): keep a newline after a nested list in the html-to-markdown ingest"`.
         The body MUST contain `Closes #410` on its own line (a closing keyword in the title
         does not link the issue), MUST link
         <https://github.com/fasrc/archi/pull/402#discussion_r3910573102>, and MUST contain
         these sections:
         **What** (one paragraph: the converter override, the two rebinds, the follower set);
         **Numbers on the live page** (before: 3 glued closers at lines 573, 775, 920; after:
         0; characters 36,948 → 36,966, text identical once whitespace is removed);
         **Persisted pages update only on force re-ingest**: the persistence layer skips an
         existing path, so the corpus count drops to 0 only after a force re-ingest; no
         re-ingest and no redeploy in this PR;
         **Golden set**: `html_to_markdown()` is the drift-hash source, so the digest moves
         only for pages with a repaired join; the bank is outside this repository;
         **Chunker tolerance stays**: `node_parsing.py` is untouched, per the issue;
         **Open PRs on the same files**: #415 adds the same class name with a `convert_pre`
         override and renames the wrapper; whichever lands second keeps one class and one
         wrapper (design, "Interplay with open PRs"); #414 and #416 overlap only at the
         test-file tail;
         **Upstream**: the repro from the proposal is ready for a human to file against
         `matthewwithanm/python-markdownify`; the loop did not file it.
         If `gh pr create` against `fasrc/archi` fails with a permissions error, leave the
         branch pushed, do **not** open a PR on any other repository, and stop.
      6. Record the PR URL as a line under this task, tick the task, and commit that edit
         with the gate. Do not merge.

      The loop pushed the branch to the fork (swinney/archi). `gh pr create --repo fasrc/archi`
      failed with "Resource not accessible by personal access token"; the loop stopped per the
      stop condition. Gate in the loop: green (3685 passed). Live: 0 glued closers, 36,966 chars.
      The nightly wrap-up (2026-09-04) pushed the same tip `42136e3b` to `origin` and opened
      the PR: https://github.com/fasrc/archi/pull/429 (host gate: 3687 passed, 1 skipped,
      1 xfailed; diff coverage 100%, 31 lines, 0 missing).
