# Design — fence multi-line bare `<code>` before conversion

## Context

`html_to_markdown()` (`src/data_manager/collectors/processing.py:211`) is a pure function
and the single extraction rule for the ingest and for golden-set drift detection. It calls
`_markdownify_deep_safe()` (`processing.py:284`), which runs
`markdownify(content, heading_style="ATX")` inside a worker thread with a 64 MiB stack and a
16,000 recursion limit, serialized by `_CONVERSION_LOCK`, because a ~2000-deep KB page (issue
#40) overflows the default limit and the C stack. Then it slices Echo-KB pages to the
article body.

`markdownify` 1.2.2 handles the two code shapes differently. `convert_pre` emits a fence,
`\n\n```<lang>\n<text>\n```\n\n`, and asks `code_language_callback(el)` for `<lang>` when
the option is set. `convert_code` emits single backticks around the converted children, and
`convert_br` inside it has already turned each `<br>` into `"  \n"`. The bare multi-line
`<code>` element therefore reaches Markdown as one inline span with hard breaks inside it,
which CommonMark cannot represent: each `# ` line becomes a heading.

Both files this change touches are black-clean and isort-clean today (checked 2026-09-02),
so the diff stays as narrow as the edit.

## Goals / Non-Goals

**Goals:**

- A multi-line bare `<code>` element converts to a fenced block with its lines intact.
- The fence carries an infostring only when the class is a known language name.
- Single-line inline `<code>`, existing `<pre>` blocks, and the deep-nesting guard are
  unchanged.
- One seam: the processor and the drift pass keep calling the same function.

**Non-Goals:**

- Empty `<h2></h2>` headings from the source HTML. That is #400.
- `<br>` inside an existing `<pre>`. `markdownify` emits a hard break there today, and 609
  blocks across 66 pages convert correctly through that path. A change there would alter
  them.
- Stripping `language-` or `lang-` prefixes from class names. The live corpus carries bare
  class names (`bash`, `lua`, `spec`; measured 2026-09-02), and the issue lists an allowlist
  only. A prefix rule is a later, measured decision.
- Any change to the `<pre>` content trimming (`strip_pre`) or the heading style.
- Re-ingest, redeploy, or any edit to the golden-set bank.

## Decisions

### D1. Normalize the HTML before conversion instead of overriding `convert_code`

The fix wraps the element in a `<pre>` in the parsed tree and lets `markdownify`'s existing
`convert_pre` emit the fence. The alternative, a `MarkdownConverter` subclass whose
`convert_code` detects `<br>` children, was rejected: by the time `convert_code` runs, the
children are already converted text with `"  \n"` breaks, so the override would have to undo
`convert_br`'s output and re-implement the fence format. A wrap before conversion keeps one
code path for every fenced block, and it gives #400 a soup pass to build on.

Prototype result against the pinned `markdownify` (2026-09-02):

| Input | Before | After |
|---|---|---|
| `<p><code class="bash">#!/bin/bash<br># comment<br>echo hi</code></p>` | `` `#!/bin/bash  \n# comment  \necho hi` `` | `` ```bash\n#!/bin/bash\n# comment\necho hi\n``` `` |
| `<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>` | `# Title\n\nAdd `--gpus=1`.` | unchanged |
| `<pre><code>#!/bin/bash\n# c\necho hi</code></pre>` | `` ```\n#!/bin/bash\n# c\necho hi\n``` `` | unchanged |
| `<p><code class="wp-block-code">line1<br>line2</code></p>` | `` `line1  \nline2` `` | `` ```\nline1\nline2\n``` `` |

### D2. The normalization runs inside `_worker()`

`_promote_block_code` is called inside the worker thread, so `BeautifulSoup(...)`,
`find_all`, and `str(soup)` run under the enlarged stack and raised limit. Measured
2026-09-02: with `beautifulsoup4` 4.12.3, the helper alone converts a 2000-deep `<div>` tree
under the default 1000-frame limit, because that version's serializer is iterative. The
placement is still a rule, not a nicety: it keeps the whole conversion one guarded unit, so
a future bs4 version or a deeper page cannot reintroduce issue #40 through the new code, and
the existing deep-nesting tests exercise the new path for free.

### D3. Skip a `<code>` that has a `<pre>` ancestor; skip one with no `<br>`

`code.find_parent("pre") is not None` means skip: `convert_pre` already fences it. No `<br>`
means skip: a single-line inline span is correct as it is, and a wrap would turn `--gpus=1`
into a block. Nested `<code>` inside `<code>` is handled by document order: the outer element
is wrapped first, and the inner one then has a `<pre>` ancestor and is skipped.

### D4. `<br>` becomes `"\n"`, and the `class` moves to the new `<pre>`

`br.replace_with("\n")` gives `convert_pre` real newlines. The `<code>` element's `class` is
copied onto the new `<pre>` because `code_language_callback` receives the `<pre>` element,
not its child.

### D5. The infostring comes from a frozenset allowlist

`_FENCE_LANGUAGES = frozenset({"bash", "sh", "spec", "lua", "python", "c", "cpp", "fortran",
"r", "perl", "json", "yaml", "text"})`. `_fence_language(pre)` iterates
`pre.get("class") or []` (a list under bs4's multi-valued attributes), lowercases each, and
returns the first member. Otherwise it returns `""`, which `convert_pre` treats as no
infostring. An arbitrary class such as `wp-block-code` or `hljs` must never reach the fence
line.

### D6. Tests live in the existing test file and pin byte-identity

All tests go in `tests/unit/test_html_to_markdown_processor.py` (issue #399, acceptance
criterion 7). The `<pre><code>` guard asserts the exact string
`'```\n#!/bin/bash\n# c\necho hi\n```'`, which is today's output (measured 2026-09-02), not a
"contains" check, so the round trip cannot drift it unnoticed. The inline guard asserts the
exact string `'# Title\n\nAdd `--gpus=1`.'`. The multi-line test asserts the fence opens
with `` ```bash ``, that the three lines appear in order with no trailing two spaces, and
that no line both starts with `#` and ends with two spaces. The helper and the callback also
get direct unit tests so a failure names the layer.

### D7. The round-trip side effect is reported, not suppressed

`str(soup)` re-serializes the page, and that alone changes the extracted text on pages with
no multi-line code (issue #399 comment: 8 of 12 pages, +944 characters, never negative;
`/kb/helmod-faq` today: +198 characters). It recovers content the current path drops. The PR
body reports it as its own line, separate from the fence numbers, and it is not treated as a
regression.

## Risks / Trade-offs

- **A short multi-line `<code>` inside a sentence becomes a block and splits the
  paragraph.** Accepted. A hard break inside an inline span has no CommonMark
  representation; today's output for that shape is already wrong.
- **`<code>` with element children other than `<br>` (for example `<span>`).**
  `convert_pre` converts children to their text inside a `<pre>`, the same as it does for a
  native `<pre><code>` today. No new behaviour.
- **An extra parse per page.** BeautifulSoup with `html.parser` on the 98 KB
  `/kb/helmod-faq` page takes well under a second, and ingest is a batch job. `markdownify`
  parses once already.
- **Every page's text and drift digest can change.** Covered by D7 and by the bank state
  (105 draft rows, no digests). The PR says so and asks for no re-ingest.
- **Recursion regression through the new code.** D2: the helper runs inside the guarded
  worker, and the existing deep-nesting tests (`-k "deeply_nested or recursion"`) must stay
  green.

## Live verification (for the close-out task and the PR body)

Run from the branch tip with the project environment. `markdown-it-py` is for verification
only. Do not import it in `src/` or `tests/`.

```bash
curl -sSL --max-time 30 https://docs.rc.fas.harvard.edu/kb/helmod-faq -o /tmp/helmod.html
python - <<'EOF'
from markdown_it import MarkdownIt
from bs4 import BeautifulSoup
from src.data_manager.collectors.processing import html_to_markdown

raw = open('/tmp/helmod.html', encoding='utf-8', errors='replace').read()
soup = BeautifulSoup(raw, 'html.parser')
expect = sum(1 for c in soup.find_all('code') if c.find_parent('pre') is None and c.find_all('br'))
md = MarkdownIt('commonmark')
text = html_to_markdown(raw)
lines = text.split('\n')
toks = md.parse(text)
heads = [t.map[0] for t in toks if t.type == 'heading_open' and t.markup.startswith('#')]
false_heads = [n + 1 for n in heads if lines[n].endswith('  ')]
fences = sum(1 for t in toks if t.type == 'fence')
print('headings          :', len(heads))
print('FALSE headings    :', len(false_heads), false_heads)
print('fenced blocks     :', fences)
print('expected fences >=:', expect)
print('chars             :', len(text))
assert not false_heads, 'still flattening code into headings'
assert fences >= expect, 'a multi-line <code> did not become a fence'
print('PASS')
EOF
```

Baseline measured 2026-09-02 on `origin/dev` `9d17fd92`: headings 40, false headings 11
(lines 41-47, 52, 57, 66, 67), fenced blocks 0, 15400 characters. Prototype of this design on
the same HTML: headings 29, false headings 0, fenced blocks 13 (`bash` x8, `lua` x3, `spec`
x2), 15697 characters.
