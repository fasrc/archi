## ADDED Requirements

### Requirement: A fenced code block's delimiter is longer than every backtick run inside it

The HTML-to-Markdown conversion SHALL open and close every fenced code block with a run of backticks whose length is `max(3, N + 1)`, where N is the length of the longest run of consecutive backticks in the block's text, so that no line inside the block can close the fence.

`markdownify` 1.2.2 `convert_pre` emits a fixed three-backtick fence. A line of three or more
backticks inside a `<pre>`, native or promoted by `_promote_block_code`, closes that fence
early: the lines after it leave the code block, a `# ` line becomes a heading, and the final
delimiter opens a new fence that swallows the rest of the page. The library's own inline rule
(`convert_code`, `max_backticks + 1`) already prevents this for spans; this requirement
extends the same rule to blocks.

The delimiter SHALL be exactly three backticks when the text contains no run of three or
more, so every block the corpus holds today converts byte-identically.

The conversion SHALL keep every other `convert_pre` behaviour of the pinned library: the
infostring from `code_language_callback`, the three `strip_pre` modes, the `ValueError` for
an invalid mode, and the empty output for an empty block.

#### Scenario: A promoted block with an embedded three-backtick line

- **WHEN** ````html_to_markdown('<p><code>a<br>```<br># heading</code></p>')```` is called
- **THEN** the output is exactly `````'````\na\n```\n# heading\n````'`````
- **AND** parsed as CommonMark, it yields one fence whose content contains the three-backtick line and zero headings

#### Scenario: A native pre block with an embedded three-backtick line

- **WHEN** ````html_to_markdown('<pre>a\n```\nb</pre>')```` is called
- **THEN** the output is exactly `````'````\na\n```\nb\n````'`````

#### Scenario: A longer run gets a longer fence

- **WHEN** `````html_to_markdown('<pre>a\n````\nb</pre>')````` is called
- **THEN** the output is exactly ``````'`````\na\n````\nb\n`````'``````

#### Scenario: The infostring rides the longer fence

- **WHEN** ````html_to_markdown('<p><code class="bash">x<br>```<br>y</code></p>')```` is called
- **THEN** the output is exactly `````'````bash\nx\n```\ny\n````'`````

#### Scenario: A mid-line run is counted

- **WHEN** ````html_to_markdown('<pre>use ``` inline</pre>')```` is called
- **THEN** the output is exactly `````'````\nuse ``` inline\n````'`````

#### Scenario: A block with no long run keeps a three-backtick fence

- **WHEN** `html_to_markdown('<pre><code>#!/bin/bash\n# c\necho hi</code></pre>')` is called
- **THEN** the output is exactly `` '```\n#!/bin/bash\n# c\necho hi\n```' ``, the same string the conversion produced before this change
- **AND** `html_to_markdown('<pre class="bash"><code>echo hi</code></pre>')` is exactly `` '```\necho hi\n```' ``
- **AND** `html_to_markdown('<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>')` is exactly `` '# Title\n\nAdd `--gpus=1`.' ``

#### Scenario: Every other convert_pre option behaves as in the pinned library

- **WHEN** the project converter and the library's `markdownify()` each convert `"<p>x</p><pre>\n\n  a\n\n</pre><p>y</p>"` with `strip_pre` set to `STRIP`, to `STRIP_ONE`, and to `None`
- **THEN** the two outputs are equal for each mode
- **AND** converting `"<pre>x</pre>"` with `strip_pre="bogus"` raises `ValueError`
- **AND** converting `"<pre></pre>"` returns `""`

#### Scenario: The conversion failure path stays testable

- **WHEN** the module-level seam that `_markdownify_deep_safe` calls is replaced by a function that raises, or by one that returns whitespace
- **THEN** `HtmlToMarkdownProcessor` returns the original resource unchanged, as the base spec requires

#### Scenario: A deeply nested page still converts

- **WHEN** a 1500-level nested `<div>` tree is converted
- **THEN** the conversion succeeds through the enlarged-stack worker
- **AND** the process-global recursion limit equals its value before the call
