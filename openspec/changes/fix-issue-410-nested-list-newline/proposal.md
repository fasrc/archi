# Keep a newline after a nested list in the HTML-to-Markdown ingest

## Why

`src/data_manager/collectors/processing.py:411` is the only conversion call in the ingest
(anchors at `origin/dev` `dc6772a2`, 2026-09-04):
`markdownify(_promote_block_code(content), heading_style="ATX", code_language_callback=_promoted_fence_language)`.
It is reached from `html_to_markdown()` (`processing.py:211`) through
`_markdownify_deep_safe()` (`processing.py:397`), inside a worker thread with an enlarged
stack. The pins are `markdownify==1.2.2` and `beautifulsoup4==4.12.3` (`pyproject.toml:54`
and `:60`).

`markdownify` 1.2.2 glues whatever follows a list nested inside a list item onto the nested
list's last line. Two library rules combine. `MarkdownConverter.convert_list` returns
`'\n' + text.rstrip()` when `'li' in parent_tags` ("remove trailing newline if we're in a
nested list"); when the last nested item ends in a `<pre>` block, the stripped newline is
the one after the closing fence. Then `process_text` strips leading whitespace from a text
node that follows a block element, and nothing puts the newline back. Measured on
2026-09-04 against the pinned version:

    >>> markdownify('<ul><li>Outer item<ul><li>Inner: <pre>x = 1</pre></li></ul>After the nested list.</li></ul>', heading_style='ATX')
    '* Outer item\n  + Inner:\n\n    ```\n    x = 1\n    ```After the nested list.'
    >>> markdownify('<ul><li>Outer item<ul><li>Inner ends in prose</li></ul>After the nested list.</li></ul>', heading_style='ATX')
    '* Outer item\n  + Inner ends in proseAfter the nested list.'
    >>> markdownify('<ul><li>A<ul><li><pre>docker rm alpine</pre></li></ul><li>Configure a bundle</li></li></ul>', heading_style='ATX')
    '* A\n  + ```\n    docker rm alpine\n    ```* Configure a bundle'

The first line is the destructive case: the closing fence and the prose share a line. A
CommonMark-strict reader treats a closing fence with trailing text as fence content, so
the fence stays open until the next opener and code and prose invert for the rest of the
page. The second line shows the same glue on prose. The third is a sibling `<li>` that the
parser places inside the parent item because the source never closed it.

Live evidence: <https://slurm.schedmd.com/containers.html>, persisted as
`websites/826095874528.md` in both the claw and dev data volumes. Re-fetched and converted
with the pinned library on 2026-09-04: 3 closing fences with trailing text (output lines
573, 775, 920), the same three the issue counted on 2026-09-02. Blast radius per the issue:
3 glued closers on 1 page of 346 (claw) and 1 of 542 (dev), and the prose form of the glue
is not counted. Every nested list that ends in a code block is exposed.

The hierarchical chunker (`src/data_manager/vectorstore/node_parsing.py`, `_closes_fence`)
deliberately accepts trailing text on a closer because of these lines. That tolerance
stays. It is the safety net for corpora ingested before this fix.

The defect is fork-local. `processing.py` and `markdownify` are absent from `upstream/dev`.
Nothing is filed upstream by this change (the loop's token cannot open issues on other
repositories); the repro above is ready for a human to file.

## What Changes

- A `MarkdownConverter` subclass `_ArchiMarkdownConverter` in
  `src/data_manager/collectors/processing.py`. It overrides `convert_list` to append the
  dropped newline when a nested list (`'li' in parent_tags`) has following content in its
  parent item whose Markdown does not start on a new line by itself. It rebinds
  `convert_ul = convert_list` and `convert_ol = convert_list`, because `markdownify` binds
  those two names to the base `convert_list` at class-definition time and a subclass
  override of `convert_list` alone is never dispatched (measured 2026-09-04: 21 shapes
  byte-identical with the naive override).
- Two module-level helpers next to the class: `_next_content_sibling(el)` returns the first
  following sibling that is a tag or a non-blank text node, skipping whitespace-only text,
  comments, and doctypes; `_nested_list_needs_break(el, text)` returns whether the newline is
  needed. A frozenset `_SELF_SEPARATING_FOLLOWERS` names the elements whose converter output
  already starts on a new line on the pinned library: `article`, `blockquote`, `br`, `div`,
  `dl`, `dt`, `figcaption`, `h1`–`h6`, `hr`, `ol`, `p`, `pre`, `section`, `table`.
- A module-level function `markdownify(html, **options)` that mirrors the library's one-line
  wrapper with the project converter, and the import line changes from
  `from markdownify import markdownify` to `from markdownify import MarkdownConverter`. The
  name stays `markdownify` because two existing tests monkeypatch
  `src.data_manager.collectors.processing.markdownify` (test file lines 120 and 130) and the
  issue requires every pre-existing test to pass unchanged. The `_worker()` body in
  `_markdownify_deep_safe` is not edited: its call already reads `markdownify(...)`.
- New tests in `tests/unit/test_html_to_markdown_processor.py`: the three issue snippets, an
  ordered-list variant, direct tests for the helpers and the dispatch, and byte-identity
  guards for the shapes that must not change (a nested list that ends its item, a following
  paragraph, code block, heading, list, or `<br>`, an empty nested list, a comment-only
  follower, and a top-level list followed by text).
- One bullet in `docs/docs/configuration.md` under the `html_to_markdown` behaviour list.

No new dependency. No change to `node_parsing.py`. No `markdownify` bump.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-processing`: one requirement is ADDED. The existing requirement "HTML-to-Markdown
  conversion at persist time" (`openspec/specs/ingest-processing/spec.md:6`) promises that
  lists survive conversion and says nothing about the join after a nested list, so its text
  stays. Issue #399's two requirements (change `fix-issue-399-fence-multiline-code`, merged
  as PR #405, not yet archived) are not in `openspec/specs/` yet, so this delta uses ADDED
  and never MODIFIED.

## Impact

- `src/data_manager/collectors/processing.py` — one import line, one class, two helpers,
  one frozenset, one wrapper function. Both files this change touches are black 24.10.0 and
  isort 6.0.1 clean today (checked 2026-09-04).
- `tests/unit/test_html_to_markdown_processor.py` — new tests appended; no existing test
  changed. The module has 55 tests today.
- `docs/docs/configuration.md` — one bullet.
- **Extracted text changes only where a nested list was glued.** On the live page the fix
  adds 18 characters (36,948 to 36,966), one newline per repaired join, and the text is
  identical once whitespace is removed. Shapes where the nested list ends the item, or where
  a paragraph, code block, heading, list, or `<br>` follows it, are byte-identical (measured
  on 21 shapes). `html_to_markdown()` is the golden-set drift-hash source, so the digest moves
  only for pages with a repaired join; the bank lives outside this repository and the loop
  cannot re-check it. The PR states this.
- **A conversion fix reaches disk only for newly ingested pages or a force-overwrite.** The
  persistence layer skips writing when the target path exists
  (`docs/docs/configuration.md`, "Applying to an existing corpus"). The persisted corpus count
  drops to 0 only after a force re-ingest. No re-ingest and no redeploy in this change.
- **Open PRs on the same two files.** PR #415 (#407, fence delimiter sizing) adds an
  `_ArchiMarkdownConverter` with a `convert_pre` override and a `_markdownify` wrapper, and it
  renames the two monkeypatch targets. This change uses the same class name on purpose.
  Whichever lands second keeps one class with both overrides and one wrapper; if #415 is on
  `dev` first, this change adds only `convert_list`, the two rebinds, and the helpers to the
  existing class and keeps `dev`'s wrapper name. PRs #414 (#406) and #416 (#408) edit
  `_promote_block_code` and `_strip_break_whitespace` only; their overlap with this change is
  the tail of the test file. Design section "Interplay with open PRs" has the rules.
