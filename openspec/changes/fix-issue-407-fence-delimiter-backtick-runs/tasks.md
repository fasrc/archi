# Tasks — fence delimiter longer than any embedded backtick run (issue #407)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail for the stated reason, write the smallest fix, run `bash scripts/gate.sh`,
commit. Never end a task with the suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** The only production file to edit is `src/data_manager/collectors/processing.py`.
  The only test file to edit is `tests/unit/test_html_to_markdown_processor.py`. Do not touch
  `pyproject.toml`, `requirements/**`, `deploy/**`, `.github/workflows/**`, `config/**`,
  `scripts/gate.sh`, `ralph.conf`, `PROMPT.md`, the `Makefile`, the `Containerfile`, or
  `docs/**`. Do not import `markdown_it` anywhere under `src/` or `tests/`. Do not edit
  `_promote_block_code`, `_strip_break_whitespace`, or `_promoted_fence_language`; issues
  #406, #408, and #410 own that ground.
- **Coverage.** `processing.py` is inside `--cov=src`, so every new line reports to
  `diff-cover` and must be covered. Task 1.2's direct converter tests exist to cover the
  `strip_pre` branches the ingest call never takes. Both files are black-clean and isort-clean
  today (checked 2026-09-03). Run `black` and `isort` on both files before `git add`, so the
  pre-commit writer cannot leave content out of the commit, and check that `git status` is
  empty after each commit.
- **Run `python -m pytest`, not bare `pytest`**, so an editable-install finder cannot resolve
  `src` to a different checkout.
- **The seam.** The only `markdownify(...)` call in `processing.py` is the one statement
  inside `_worker()` of `_markdownify_deep_safe` (`processing.py:411`). The new converter is
  constructed there, inside the worker thread (design D4). Nothing moves outside the
  `with _CONVERSION_LOCK` block.
- **Expected strings** for every test are in design D5. They were measured with a prototype
  on `36fdb420`, 2026-09-03. Assert exact equality, not `in`.
- **Known flake.** `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_terminates_running_evaluation_process`
  has raced under CPU load in earlier runs and is unrelated to this change. If the gate
  fails on only that test, re-run it once.

## 1. The converter and the seam

- [ ] 1.1 `model: sonnet` — RED tests first, in `tests/unit/test_html_to_markdown_processor.py`,
      both through `html_to_markdown()`:
      (a) ````html_to_markdown('<p><code>a<br>```<br># heading</code></p>')```` equals
      `````'````\na\n```\n# heading\n````'````` exactly. Today it returns
      `````'```\na\n```\n# heading\n```'`````, so this fails on the assertion;
      (b) ````html_to_markdown('<pre>a\n```\nb</pre>')```` equals `````'````\na\n```\nb\n````'`````
      exactly. Today it returns `````'```\na\n```\nb\n```'`````.
      Watch both fail on the assertion. Then implement design D1 to D4 in
      `src/data_manager/collectors/processing.py`, all in one turn because the existing
      failure-path tests break the moment the import changes:
      1. Replace `from markdownify import markdownify` (`processing.py:33`) with
         `from markdownify import STRIP, STRIP_ONE, MarkdownConverter, strip1_pre, strip_pre`
         (isort will order the names).
      2. Directly above `_markdownify_deep_safe`, add `_BACKTICK_RUNS = re.compile(r"`+")`
         and the class `_ArchiMarkdownConverter(MarkdownConverter)` with a docstring that
         names issue #407 and says this class is the one place for project overrides of
         `MarkdownConverter` (so #410 adds `convert_list` here). Its only method is
         `convert_pre(self, el, text, parent_tags)`: the 1.2.2 body copied (`if not text:
         return ""`; `code_language = self.options["code_language"]`; the
         `code_language_callback` branch; `if mode == STRIP: text = strip_pre(text)` /
         `elif mode == STRIP_ONE: text = strip1_pre(text)` / `elif mode is None: pass` /
         `else: raise ValueError("Invalid value for strip_pre: %s" % mode)`), then
         `longest_run = max((len(m) for m in _BACKTICK_RUNS.findall(text)), default=0)`,
         `fence = "`" * max(3, longest_run + 1)`, and
         `return "\n\n%s%s\n%s\n%s\n\n" % (fence, code_language, text, fence)`.
      3. Below the class, add the seam
         `def _markdownify(html: str, **options) -> str: return _ArchiMarkdownConverter(**options).convert(html)`
         with a one-line docstring saying it mirrors the library's `markdownify()` with the
         project converter.
      4. In `_worker()`, change the callee from `markdownify(` to `_markdownify(`. Change
         nothing else in that function.
      5. In the test module, change the monkeypatch target string in
         `test_converter_raises_keeps_original` and `test_blank_output_keeps_original` from
         `"src.data_manager.collectors.processing.markdownify"` to
         `"src.data_manager.collectors.processing._markdownify"`. Change nothing else in
         those two tests.
      Run `python -m pytest tests/unit/test_html_to_markdown_processor.py -q` (all green,
      including the two retargeted tests and the byte-identity guards
      `test_wire_guard_inline_code_unchanged`, `test_wire_guard_pre_code_unchanged`,
      `test_wire_guard_native_pre_with_language_class_unchanged`) and
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -k "deeply_nested or recursion" -q`
      (still green: issue #40 is not reintroduced). Gate green; commit.
- [ ] 1.2 `model: sonnet` — Guards and direct converter tests. **These pass once 1.1 lands;
      that is the point of them. Do not contrive a failure first.** Add to the same test
      module, importing `_markdownify` by name in the module's existing
      `from src.data_manager.collectors.processing import (...)` block and
      `from markdownify import STRIP, STRIP_ONE, markdownify as library_markdownify` next to
      the other third-party imports:
      (a) `````html_to_markdown('<pre>a\n````\nb</pre>')````` equals
      ``````'`````\na\n````\nb\n`````'`````` (a four-run gets a five-backtick fence);
      (b) ````html_to_markdown('<p><code class="bash">x<br>```<br>y</code></p>')```` equals
      `````'````bash\nx\n```\ny\n````'````` (the infostring rides the longer fence);
      (c) ````html_to_markdown('<pre>use ``` inline</pre>')```` equals
      `````'````\nuse ``` inline\n````'````` (a mid-line run is counted, design D2);
      (d) `html_to_markdown('<p>x</p><pre></pre><p>y</p>')` equals `'x\n\ny'` and
      `_markdownify("<pre></pre>")` equals `""` (the empty-block branch);
      (e) for `html = "<p>x</p><pre>\n\n  a\n\n</pre><p>y</p>"` and each `mode` in
      `(STRIP, STRIP_ONE, None)`, `_markdownify(html, strip_pre=mode)` equals
      `library_markdownify(html, strip_pre=mode)` (a `pytest.mark.parametrize` over the three
      modes is fine);
      (f) `_markdownify("<pre>x</pre>", strip_pre="bogus")` raises `ValueError`
      (`pytest.raises`).
      Run the test module, then confirm patch coverage is complete:
      `python -m pytest tests/unit/test_html_to_markdown_processor.py -q --cov=src.data_manager.collectors.processing --cov-report=term-missing`
      (the module-name form; a file path collects nothing) must list no missing line inside
      `_ArchiMarkdownConverter.convert_pre` or `_markdownify`. Gate green; commit.

## 2. Close out

- [ ] 2.1 `model: sonnet` — Verify, push, and open the PR. Steps, in order:
      1. `bash scripts/gate.sh` on the finished branch exits 0. `git status` is empty.
      2. `git diff origin/dev --stat` lists only `src/data_manager/collectors/processing.py`,
         `tests/unit/test_html_to_markdown_processor.py`, and this change's
         `openspec/changes/fix-issue-407-fence-delimiter-backtick-runs/` files. Nothing under
         `pyproject.toml`, `requirements/`, or `docs/`.
      3. Run the script under "Verification" in this change's `design.md`. It needs the
         `markdown_it` package, which the `archi-loop` image carries (4.2.0, checked
         2026-09-03). If the import fails anyway, do not fail the task; write the reason in
         the PR body.
      4. Push: `git push -u origin fix/issue-407-fence-delimiter-backtick-runs`. The branch
         tracks `origin/dev` until then, so `-u` is required and a bare `git push` is refused.
      5. Open the PR with
         `gh pr create --repo fasrc/archi --base dev --title "fix(#407): size the fence delimiter to the longest backtick run in the block"`.
         The body MUST contain `Closes #407` on its own line (a closing keyword in the title
         does not link the issue), and MUST contain these sections:
         **What** (one paragraph: the subclass, the seam, the one-token call change);
         **Measured outputs** (the before/after table from design D5, or the two defect rows
         of it, plus the three byte-identity guards);
         **Corpus** (0 of 25 promoted and 0 of 145 native `<pre>` in the 60-page sample carry
         a run of three or more backticks, so persisted text changes for no page in the
         sample; a re-ingest is not requested);
         **Verification** (the `markdown-it-py` result: `PASS`, or the reason it did not run);
         **Related** (PR #405 and its Codex thread
         <https://github.com/fasrc/archi/pull/405#discussion_r3912257990>; PR #414 for #406
         on the same file, whichever merges second rebases; #410 adds `convert_list` to
         `_ArchiMarkdownConverter`);
         **No re-ingest and no redeploy in this PR.**
         If `gh pr create` against `fasrc/archi` fails with a permissions error, leave the
         branch pushed, do **not** open a PR on any other repository, and stop.
      6. Record the PR URL as a line under this task, tick the task, and commit that edit
         with the gate. Do not merge.
