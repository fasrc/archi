# Choose a fence delimiter longer than any embedded backtick run

## Why

`src/data_manager/collectors/processing.py:411` is the only conversion call in the ingest:
`markdownify(_promote_block_code(content), heading_style="ATX",
code_language_callback=_promoted_fence_language)`, inside `_worker()` of
`_markdownify_deep_safe` (`processing.py:397`). The pin is `markdownify==1.2.2`
(`pyproject.toml:54`, `requirements/requirements-base.txt:5`).

`markdownify` 1.2.2 `convert_pre` always emits a three-backtick fence,
`'\n\n```%s\n%s\n```\n\n'`. Its `convert_code` already sizes an inline span's delimiter to
`max_backticks + 1`, so only the block path has the gap. Every `<pre>` on the ingest path
shares it: a native `<pre>` block and the `<pre>` that `_promote_block_code` (PR #405,
issue #399) creates from a multi-line bare `<code>`.

Measured on `origin/dev` `36fdb420` (2026-09-03), through the project's own
`html_to_markdown()`:

    html_to_markdown('<p><code>a<br>```<br># heading</code></p>')  ->  '```\na\n```\n# heading\n```'
    html_to_markdown('<pre>a\n```\nb</pre>')                        ->  '```\na\n```\nb\n```'
    html_to_markdown('<pre>a\n````\nb</pre>')                       ->  '```\na\n````\nb\n```'

The embedded run closes the fence early. `# heading` becomes a heading, and the last
delimiter opens a new fence that swallows whatever follows on the page. Parsed with
`markdown-it-py` 4.2.0 in commonmark mode, the first output yields 2 fence tokens and 1
heading token where the source has 1 code block and 0 headings.

The harm is latent today. In a 60-page sample of the 213 KB pages (issue #407, measured
2026-09-02), 0 of 25 promoted elements and 0 of 145 native `<pre>` elements contain a line of
three or more backticks. That is why PR #405 deferred the fix. It remains a correctness gap in
the one extraction rule that the ingest and the golden-set drift pass share, the fix is local
to one function, and Codex raised it on PR #405
(<https://github.com/fasrc/archi/pull/405#discussion_r3912257990>).

The gap is in the pinned library. An upstream report is optional and is not part of this
change.

## What Changes

- A new `_ArchiMarkdownConverter(MarkdownConverter)` class in
  `src/data_manager/collectors/processing.py` that overrides `convert_pre` only. The body is
  the 1.2.2 body (`code_language`, the callback, the three `strip_pre` branches) with the fixed
  delimiter replaced by `` "`" * max(3, longest_run + 1) ``, where `longest_run` is the length
  of the longest run of consecutive backticks anywhere in the block text.
- A new module-level seam `_markdownify(html: str, **options) -> str` that returns
  `_ArchiMarkdownConverter(**options).convert(html)`. The library's own `markdownify()` is
  exactly `MarkdownConverter(**options).convert(html)` (verified on 1.2.2, 2026-09-03), so
  the seam changes only the class.
- The import `from markdownify import markdownify` becomes
  `from markdownify import STRIP, STRIP_ONE, MarkdownConverter, strip1_pre, strip_pre`.
- The one call inside `_worker()` calls `_markdownify(...)` with the same three arguments.
- In `tests/unit/test_html_to_markdown_processor.py`, the two failure-path tests
  (`test_converter_raises_keeps_original`, `test_blank_output_keeps_original`) retarget their
  monkeypatch from `processing.markdownify` to `processing._markdownify`. New tests cover the
  two defect shapes, a longer run, the infostring on a longer fence, the byte-identity guards,
  and the converter's option handling.

No new dependency. No file outside those two and this change directory.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-processing`: one requirement is ADDED. The two requirements from #399 live in the
  unarchived change `openspec/changes/fix-issue-399-fence-multiline-code/`, not yet in
  `openspec/specs/ingest-processing/spec.md`, so this delta adds its own requirement and does
  not modify one that the base spec does not contain.

## Impact

- `src/data_manager/collectors/processing.py`: one import line, one regex, one class with one
  method, one seam function, one call-site name.
- `tests/unit/test_html_to_markdown_processor.py`: two monkeypatch targets retargeted, new
  tests added, no other existing test changed.
- **Persisted text is unchanged for every block whose text has no run of three or more
  backticks.** For a run of 0, 1, or 2 backticks the delimiter is `max(3, run + 1) == 3`,
  the same string as today. The byte-identity guards for inline `<code>`, native `<pre>`, and
  `<pre class="bash">` pin this. The sample has zero blocks with a longer run, so no page in
  the corpus needs a re-ingest for this fix, and none is requested.
- **Golden set.** `html_to_markdown()` is the drift-hash source. Its output moves only for a
  page that has a block with a run of three or more backticks. The sample has none.
- **Related work on the same file.** PR #414 (issue #406) is open on `_promote_block_code`;
  whichever merges second rebases. Issue #408 changes `_strip_break_whitespace`. Issue #410
  calls for a `MarkdownConverter` subclass with a `convert_list` override; it must add that
  method to `_ArchiMarkdownConverter` rather than create a second class, which is why the
  class carries a general name and not a fence-specific one.
- No re-ingest and no redeploy in this change.
