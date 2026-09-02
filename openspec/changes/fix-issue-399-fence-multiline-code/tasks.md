# Tasks — fence multi-line bare `<code>` in the HTML-to-Markdown ingest

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail for the stated reason, write the smallest fix, run `bash scripts/gate.sh`,
commit. Never end a task with the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** The only production file to edit is `src/data_manager/collectors/processing.py`.
  The only test file to edit is `tests/unit/test_html_to_markdown_processor.py`. Do not touch
  `pyproject.toml`, `requirements/**`, `deploy/**`, `.github/workflows/**`, `config/**`,
  `scripts/gate.sh`, `ralph.conf`, `PROMPT.md`, the `Makefile`, or the `Containerfile`. Do
  not import `markdown_it` anywhere under `src/` or `tests/`.
- **Coverage.** `processing.py` is inside `--cov=src`, so every new line reports to
  `diff-cover` and must be covered. Both files are black-clean and isort-clean today. Keep
  them so, and run `black` and `isort` on both files before `git add`, so the pre-commit
  writer cannot leave content out of the commit. Check that `git status` is empty after each
  commit.
- **Run `python -m pytest`, not bare `pytest`**, so an editable-install finder cannot resolve
  `src` to a different checkout.
- **The seam.** `_markdownify_deep_safe` (`processing.py:284`) runs the conversion in its
  inner `_worker()`. The only `markdownify(...)` call in the file is inside that function.
  The normalization goes inside `_worker()`, not before the `with _CONVERSION_LOCK` block
  (design D2).
- **Known flake.** `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_terminates_running_evaluation_process`
  has raced under CPU load in earlier runs and is unrelated to this change. If the gate
  fails on only that test, re-run it once.

## 1. The two helpers, each with direct tests

- [x] 1.1 `model: opus` — Add `_promote_block_code(html: str) -> str` to
      `src/data_manager/collectors/processing.py` as a module-level function placed directly
      above `_markdownify_deep_safe`, with a docstring that names issue #399 and the shape it
      fixes. RED tests first, in `tests/unit/test_html_to_markdown_processor.py`. Import the
      helper by name in the module's existing `from src.data_manager.collectors.processing
      import (...)` block, so the red is an `ImportError`. Parse each result with
      `BeautifulSoup(result, "html.parser")` and assert on the tree, not on the exact string:
      (a) `_promote_block_code('<p><code class="bash">a<br>b</code></p>')` — the `<code>`
      element now has a `<pre>` parent whose `class` is `["bash"]`, the `<code>` text is
      `"a\nb"`, and no `<br>` remains;
      (b) `_promote_block_code('<p>Add <code>--gpus=1</code>.</p>')` — the `<code>` element
      has no `<pre>` ancestor;
      (c) `_promote_block_code('<pre><code>a<br>b</code></pre>')` — exactly one `<pre>`, and
      the `<br>` is still there (a `<code>` under a `<pre>` is skipped);
      (d) `_promote_block_code('<p><code>a<br>b</code></p>')` — the new `<pre>` has no
      `class` attribute.
      Watch all four fail on the import. Then implement per design D3 and D4: parse with
      `BeautifulSoup(html, "html.parser")`; for each `code` in `soup.find_all("code")`, skip
      when `code.find_parent("pre") is not None`, skip when `code.find_all("br")` is empty,
      otherwise call `br.replace_with("\n")` for each `<br>`, create `soup.new_tag("pre")`,
      copy `class` when present, and `code.wrap(pre)`; return `str(soup)`. Gate green;
      commit.
- [x] 1.2 `model: sonnet` — Add `_FENCE_LANGUAGES` (a `frozenset` of exactly `bash`, `sh`,
      `spec`, `lua`, `python`, `c`, `cpp`, `fortran`, `r`, `perl`, `json`, `yaml`, `text`)
      and `_fence_language(pre) -> str` to `processing.py`, next to the helper from 1.1. RED
      tests first, importing both by name: build `<pre>` tags with
      `BeautifulSoup('<pre class="...">x</pre>', "html.parser").pre` and assert
      `_fence_language(...)` returns `"bash"` for `class="bash"`, `"bash"` for
      `class="hljs bash"`, `"bash"` for `class="Bash"`, `""` for `class="wp-block-code"`,
      and `""` for a `<pre>` with no class. Also assert `_FENCE_LANGUAGES` equals the
      thirteen-name set above. Watch them fail on the import. Implement per design D5:
      iterate `pre.get("class") or []`, lowercase each, return the first member of
      `_FENCE_LANGUAGES`, else `""`. Gate green; commit.

## 2. Wire the helpers into the conversion

- [x] 2.1 `model: opus` — RED tests first, all through `html_to_markdown()` and the
      processor, in the same test file:
      (a) `html_to_markdown('<p><code class="bash">#!/bin/bash<br># comment<br>echo hi</code></p>')`
      contains `"```bash\n#!/bin/bash\n# comment\necho hi\n```"`, and no line of it both
      starts with `#` and ends with two spaces. Today it returns
      `` '`#!/bin/bash  \n# comment  \necho hi`' ``, so this fails on the assertion;
      (b) `html_to_markdown('<p><code class="wp-block-code">line1<br>line2</code></p>')`
      contains `"```\nline1\nline2\n```"` and does not contain `wp-block-code`. Fails today;
      (c) guard: `html_to_markdown('<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>')`
      equals `'# Title\n\nAdd `--gpus=1`.'` exactly. Passes today and must keep passing;
      (d) guard: `html_to_markdown('<pre><code>#!/bin/bash\n# c\necho hi</code></pre>')`
      equals `'```\n#!/bin/bash\n# c\necho hi\n```'` exactly. Passes today and must keep
      passing;
      (e) `HtmlToMarkdownProcessor().process(_html_resource(content=<the (a) HTML>))` has
      suffix `md`, and its `get_content()` equals `html_to_markdown(<the (a) HTML>)` and
      contains `"```bash"`. Fails today on the fence assertion.
      Do not contrive a failure for (c) or (d); they are guards. Watch (a), (b), and (e)
      fail. Then change the one statement inside `_worker()` to
      `result["value"] = markdownify(_promote_block_code(content), heading_style="ATX", code_language_callback=_fence_language)`
      and nothing else in that function (black will wrap that call over several lines; that
      is fine). Run `python -m pytest tests/unit/test_html_to_markdown_processor.py -q`
      (all green) and
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -k "deeply_nested or recursion" -q`
      (still green: issue #40 is not reintroduced). Gate green; commit.

## 3. Close out

- [ ] 3.1 `model: sonnet` — Verify, push, and open the PR. Steps, in order:
      1. `bash scripts/gate.sh` on the finished branch exits 0. `git status` is empty.
      2. `git diff origin/dev --stat` lists only `src/data_manager/collectors/processing.py`,
         `tests/unit/test_html_to_markdown_processor.py`, and this change's
         `openspec/changes/fix-issue-399-fence-multiline-code/` files. Nothing under
         `pyproject.toml` or `requirements/`.
      3. Live verification, best effort: run the script under "Live verification" in this
         change's `design.md`. It needs network access to `docs.rc.fas.harvard.edu` and the
         `markdown_it` package (`pip install 'markdown-it-py==4.2.0'` if absent). If either
         is unavailable, do not fail the task. Write the reason in the PR body and use the
         2026-09-02 baseline and prototype numbers from `design.md`.
      4. Push: `git push -u origin fix/issue-399-fence-multiline-code`.
      5. Open the PR with
         `gh pr create --repo fasrc/archi --base dev --title "fix(#399): fence multi-line bare code in the html-to-markdown ingest"`.
         The body MUST contain `Closes #399` on its own line (a closing keyword in the title
         does not link the issue), and MUST contain these sections:
         **What** (one paragraph);
         **Code-fence numbers on `/kb/helmod-faq`** (before: 40 headings / 11 false / 0
         fences; after: the measured headings / 0 false / the fence count, matched to the
         re-counted multi-line `<code>` elements);
         **Round-trip side effect**, on its own, stating that the BeautifulSoup
         re-serialization changes extracted text on pages with no multi-line code and is
         additive (issue #399 comment: 8 of 12 pages, +944 chars; helmod-faq today: +198
         chars), not a regression;
         **Golden set**: `html_to_markdown()` is the drift-hash source, the bank's 105 rows
         are all `draft` with no digest per the issue's comment, so no locked digest is
         invalidated;
         **No re-ingest and no redeploy in this PR**: re-ingest is a separate operator
         decision because it re-scrapes the corpus;
         **Related**: #400 shares the function and should build on `_promote_block_code`'s
         soup pass.
         If `gh pr create` against `fasrc/archi` fails with a permissions error, leave the
         branch pushed, do **not** open a PR on any other repository, and stop.
      6. Record the PR URL as a line under this task, tick the task, and commit that edit
         with the gate. Do not merge.
