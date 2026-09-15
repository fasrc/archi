## ADDED Requirements

### Requirement: Source whitespace beside a promoted break is dropped through inline nodes

The HTML-to-Markdown conversion SHALL, when it promotes a bare multi-line `<code>` element to a fenced block, drop the source newline that sits beside each `<br>` even when that newline is inside an inline child tag next to the break or after the inline tag whose last child is the break, so that a wrapped source line never becomes a blank line inside the fence.

The promotion already drops a source newline that is a direct text neighbour of the break
(PR #405, `_strip_break_whitespace`). When the neighbour is a tag such as `<span>`, or when
the break is the last child of a `<span>` and the newline follows that `<span>`, the newline
survives and `<p><code>a<br><span>\nb</span></code></p>` converts to `` ```\na\n\nb\n``` ``.
The source element is inline, so a browser collapses that newline; it is formatting, not
content.

The resolution SHALL look through a tag neighbour to the one text node that touches the
break, by walking the tag's first children (next side) or last children (previous side), SHALL
skip an HTML comment inside that tag, SHALL leave the whitespace behind a childless tag at
the edge (such as `<img>`) untouched because it does not touch the break, SHALL climb to the
parent's sibling when the break has no sibling on a side, and SHALL stop at the `<code>`
element being promoted so that text after the element is never touched. Horizontal
whitespace after the dropped newline SHALL be kept. The rule SHALL run in the same strip pass,
before any break is replaced, inside the deep-safe worker with the rest of the promotion.

#### Scenario: A newline inside an inline tag after the break is dropped

- **WHEN** `html_to_markdown('<p><code>a<br><span>\nb</span></code></p>')` is called
- **THEN** the output is exactly `` ```\na\nb\n``` ``
- **AND** the fence contains no blank line

#### Scenario: A newline after an inline tag that ends with the break is dropped

- **WHEN** `html_to_markdown('<p><code><span>a<br></span>\nb</code></p>')` is called
- **THEN** the output is exactly `` ```\na\nb\n``` ``

#### Scenario: Indentation after the dropped newline is kept

- **WHEN** `html_to_markdown('<p><code>a<br><span>\n    b</span></code></p>')` is called
- **THEN** the output is exactly `` ```\na\n    b\n``` ``

#### Scenario: The climb passes through nested inline tags

- **WHEN** `html_to_markdown('<p><code><span><em>a<br></em></span>\nb</code></p>')` is called
- **THEN** the output is exactly `` ```\na\nb\n``` ``

#### Scenario: The previous side is resolved the same way

- **WHEN** `html_to_markdown('<p><code>a\n<span><br>b</span></code></p>')` is called
- **THEN** the output is exactly `` ```\na\nb\n``` ``

#### Scenario: A comment inside the inline tag is skipped

- **WHEN** `html_to_markdown('<p><code>a<br><span><!-- c -->\nb</span></code></p>')` is called
- **THEN** the output is exactly `` ```\na\nb\n``` ``

#### Scenario: Whitespace behind a childless tag at the edge is kept

- **WHEN** `html_to_markdown('<p><code>a<br><span><img src="i"/>\nb</span></code></p>')` is called
- **THEN** the output is exactly `` ```\na\n![](i)\nb\n``` ``
- **AND** `html_to_markdown('<p><code><span>a\n<img src="i"/></span><br>b</code></p>')` is exactly `` ```\na\n![](i)\nb\n``` ``

#### Scenario: The climb never passes the code element

- **WHEN** `_promote_block_code('<p><code>a<br></code>\nmore</p>')` is called
- **THEN** the last child of the `<p>` element in the result is still the text `"\nmore"`

#### Scenario: Two breaks still keep one blank line

- **WHEN** `html_to_markdown('<p><code>a<br><br>b</code></p>')` is called
- **THEN** the output is exactly `` ```\na\n\nb\n``` ``, the same string the conversion produced before this change
- **AND** `html_to_markdown('<p><code>a<br />\n<br />\nb</code></p>')` is also exactly `` ```\na\n\nb\n``` ``

#### Scenario: The existing break and fence guards are unchanged

- **WHEN** `html_to_markdown('<p><code class="bash">#!/bin/bash<br />\n# comment<br />\necho hi</code></p>')` is called
- **THEN** the output is exactly `` ```bash\n#!/bin/bash\n# comment\necho hi\n``` ``
- **AND** `html_to_markdown('<p><code>all:<br>\n\tcc main.c</code></p>')` is exactly `` ```\nall:\n\tcc main.c\n``` ``
- **AND** `html_to_markdown('<p><code>a\tb\t<br>c</code></p>')` is exactly `` ```\na\tb\t\nc\n``` ``
- **AND** `html_to_markdown('<pre class="bash"><code>echo hi</code></pre>')` is exactly `` ```\necho hi\n``` ``
- **AND** `html_to_markdown('<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>')` is exactly `# Title\n\nAdd `--gpus=1`.`

#### Scenario: A deeply nested page still converts

- **WHEN** a 2000-level nested `<div>` tree is processed
- **THEN** the resource is converted to Markdown with suffix `md`
- **AND** the process-global recursion limit equals its value before the call
