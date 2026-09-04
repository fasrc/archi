# Design — a fence delimiter that outlasts every backtick run inside the block

## Context

`html_to_markdown()` (`src/data_manager/collectors/processing.py:211`) is a pure function and
the single extraction rule for the ingest and for golden-set drift detection. It calls
`_markdownify_deep_safe()` (`processing.py:397`), which runs the conversion inside a worker
thread with an enlarged stack and a raised recursion limit, serialized by `_CONVERSION_LOCK`
(issue #40). Since PR #405 the worker's one statement is

    result["value"] = markdownify(
        _promote_block_code(content),
        heading_style="ATX",
        code_language_callback=_promoted_fence_language,
    )

`markdownify` 1.2.2 `convert_pre` (`site-packages/markdownify/__init__.py`, line 688) reads
`code_language`, asks `code_language_callback(el)` when set, applies one of three `strip_pre`
modes (`STRIP`, `STRIP_ONE`, `None`; anything else raises `ValueError`), and returns
`'\n\n```%s\n%s\n```\n\n' % (code_language, text)`. The delimiter is fixed at three backticks.
`convert_code` (line 485) already computes `max_backticks + 1` for inline spans with
`re_backtick_runs`, so the library has the rule; it applies it to one of the two code paths.

The library's `markdownify(html, **options)` is exactly `MarkdownConverter(**options).convert(html)`
(read with `inspect.getsource`, 2026-09-03).

Both files this change touches are black-clean and isort-clean today (checked 2026-09-03 with
black 24.10.0 and isort 6.0.1).

## Goals / Non-Goals

**Goals:**

- A fenced block whose text contains a run of N backticks (N >= 3) opens and closes with a
  run of N + 1 backticks, so no line inside it can close the fence.
- A block with no such run converts byte-identically to today.
- The infostring, the `strip_pre` modes, and the empty-block case behave as in 1.2.2.
- The two existing failure-path tests keep a module-level seam to monkeypatch.
- One subclass, named for the project, so #410 adds its `convert_list` override to the same
  class.

**Non-Goals:**

- Source newlines inside inline tags beside a promoted `<br>`. That is #408.
- The missing newline after a nested list. That is #410.
- Tilde fences. `markdownify` never emits them and a backtick run cannot close one; there is
  no reason to switch delimiter character.
- Escaping or altering backticks inside the block text. The text is content.
- Any change to `strip_pre` defaults, heading style, or `_promote_block_code`.
- Documentation edits. Issue #407, acceptance criterion 7, limits the diff to the two source
  files and this change directory.

## Decisions

### D1. Subclass `MarkdownConverter` and override `convert_pre` only

`_ArchiMarkdownConverter(MarkdownConverter)` gets one method, `convert_pre`. The body is the
1.2.2 body copied, with the last line changed. The copy is short (about 15 lines) and every
branch of it is pinned by a test (D5), so a future `markdownify` bump that changes
`convert_pre` shows up as a test failure, not as silent drift.

Rejected: call `super().convert_pre(...)` and rewrite the delimiters in its return value. That
depends on the exact shape of the returned string (`'\n\n```'` prefix, `'\n```\n\n'` suffix)
rather than on documented options, and is no less coupled to the pinned version than the copy.

Rejected: override `convert_code` too. Its delimiter rule is already right.

The class name is general on purpose. Issue #410 prescribes a `MarkdownConverter` subclass
with a `convert_list` override on the same call site; it adds a method here.

### D2. Delimiter length is `max(3, longest_run + 1)` over the whole block text

    _BACKTICK_RUNS = re.compile(r"`+")
    longest_run = max((len(m) for m in _BACKTICK_RUNS.findall(text)), default=0)
    fence = "`" * max(3, longest_run + 1)

The run is measured anywhere in the text, not only at line starts. CommonMark closes a fence
only on a line that starts (after up to three spaces) with a run at least as long as the
opener, so a mid-line run cannot close it and this rule is conservative: `<pre>use ``` inline</pre>`
gets a four-backtick fence it did not strictly need. That is the same rule `convert_code`
applies, it is one regex instead of a line-structure parser, and the extra backtick is
harmless to every CommonMark consumer. The `max(3, ...)` floor keeps runs of 0, 1, or 2 at
exactly three, which is the byte-identity guarantee.

The run is measured after the `strip_pre` step. Stripping removes only leading and trailing
newlines, so the count is the same either way; measuring last keeps the code in reading
order.

### D3. A module-level seam, `_markdownify`, replaces the imported name

    def _markdownify(html: str, **options) -> str:
        return _ArchiMarkdownConverter(**options).convert(html)

`test_converter_raises_keeps_original` and `test_blank_output_keeps_original`
(`tests/unit/test_html_to_markdown_processor.py:115-135`) monkeypatch
`src.data_manager.collectors.processing.markdownify`. A bare swap of the call to a class
would leave those tests patching a name the worker no longer reads. Once the import is
gone the string target raises `AttributeError` and both tests error; had the import stayed,
both would pass while testing nothing. The seam keeps the failure paths testable, and the two
tests retarget to `processing._markdownify`. That is the only edit to an existing test.

Rejected: keep the name `markdownify` for the wrapper. It would shadow the library function
it replaces and read as the library call at the one site that matters.

### D4. The seam is called inside `_worker()`, with the same arguments

The worker's statement changes in one token, `markdownify` to `_markdownify`. The
`heading_style` and `code_language_callback` arguments stay. The converter is constructed
inside the worker thread, under the enlarged stack and the raised limit, so the deep-nesting
guard (`-k "deeply_nested or recursion"`) exercises the new class for free.

### D5. Tests pin exact strings and every branch of the copied body

All tests go in `tests/unit/test_html_to_markdown_processor.py`. Expected strings below were
measured with a prototype of this design on `36fdb420`, 2026-09-03.

Through `html_to_markdown()`:

| Input | Today | After |
|---|---|---|
| ````<p><code>a<br>```<br># heading</code></p>```` | `````'```\na\n```\n# heading\n```'````` | `````'````\na\n```\n# heading\n````'````` |
| ````<pre>a\n```\nb</pre>```` | `````'```\na\n```\nb\n```'````` | `````'````\na\n```\nb\n````'````` |
| `````<pre>a\n````\nb</pre>````` | ``````'```\na\n````\nb\n```'`````` | ``````'`````\na\n````\nb\n`````'`````` |
| ````<p><code class="bash">x<br>```<br>y</code></p>```` | `````'```bash\nx\n```\ny\n```'````` | `````'````bash\nx\n```\ny\n````'````` |
| ````<pre>use ``` inline</pre>```` | `````'```\nuse ``` inline\n```'````` | `````'````\nuse ``` inline\n````'````` |
| `<pre><code>#!/bin/bash\n# c\necho hi</code></pre>` | `` '```\n#!/bin/bash\n# c\necho hi\n```' `` | unchanged |
| `<pre class="bash"><code>echo hi</code></pre>` | `` '```\necho hi\n```' `` | unchanged |
| `<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>` | `` '# Title\n\nAdd `--gpus=1`.' `` | unchanged |
| `<p>x</p><pre></pre><p>y</p>` | `'x\n\ny'` | unchanged |

Directly on the converter, to cover the branches the ingest call never takes and to prove
the copied body is faithful: for `html = "<p>x</p><pre>\n\n  a\n\n</pre><p>y</p>"` and each
mode in `(STRIP, STRIP_ONE, None)`, `_markdownify(html, strip_pre=mode)` equals the library's
`markdownify(html, strip_pre=mode)` (measured equal for all three);
`_markdownify("<pre>x</pre>", strip_pre="bogus")` raises `ValueError`; and
`_markdownify("<pre></pre>")` returns `""`. With these, every line of the new code is
executed, so `diff-cover` reports 100 percent patch coverage rather than a number near the
80 percent floor.

### D6. `markdown-it-py` is verification only

`markdown-it-py` 4.2.0 is installed in the project environment and in the `archi-loop`
image (checked 2026-09-03). The close-out task parses the two defect outputs with it and
asserts one fence token, zero heading tokens, and the embedded run inside the fence content.
It is never imported under `src/` or `tests/`; the unit tests assert exact strings instead.

## Risks / Trade-offs

- **A longer fence where none was strictly needed** (mid-line run, D2). Accepted. Valid
  CommonMark, and the sample corpus has no such block.
- **Copied library code.** About 15 lines duplicated from a pinned dependency. Accepted: the
  three `strip_pre` branches and the invalid-mode error are each pinned by an equivalence or
  `raises` test against the same pinned version, so a bump cannot change them unnoticed.
- **Merge overlap.** PR #414 (issue #406) edits `_promote_block_code` in the same file and
  the same test module. This change does not touch that function. Whichever merges second
  rebases; the conflicts, if any, are in the import block and the test module's import list.
- **Two existing tests edited.** Only their monkeypatch target string changes (D3). Their
  assertions are untouched.

## Verification (for the close-out task and the PR body)

Run from the branch tip with the project environment.

```bash
python - <<'EOF'
import sys; sys.path.insert(0, "")
from markdown_it import MarkdownIt
from src.data_manager.collectors.processing import html_to_markdown
md = MarkdownIt("commonmark")
for html in ('<p><code>a<br>```<br># heading</code></p>', '<pre>a\n```\nb</pre>'):
    toks = md.parse(html_to_markdown(html))
    fences = [t for t in toks if t.type == "fence"]
    heads = [t for t in toks if t.type == "heading_open"]
    assert len(fences) == 1 and not heads and "```" in fences[0].content, (html, fences, heads)
print("PASS")
EOF
```

Before the fix on `36fdb420` the first input parses to 2 fences and 1 heading. After the fix,
the script prints `PASS`.
