## ADDED Requirements

### Requirement: A promoted code block leaves its inline formatting ancestors

The HTML-to-Markdown conversion SHALL move a promoted multi-line `<code>` block out of every enclosing inline tag that the converter renders with markup (`a`, `b`, `strong`, `em`, `i`, `del`, `s`, `kbd`, `samp`, `sub`, `sup`) by splitting each such ancestor around the block, so that the fence opens and closes on lines of its own and the sibling text on either side keeps its formatting.

`_promote_block_code` wraps the `<code>` in place. When that element sits under an inline tag
with a markup converter, `markdownify` 1.2.2 wraps the whole fence in the tag's markers:
`<p><em><code>a<br># heading</code></em></p>` converts to `'*```\na\n# heading\n```*'`, where
`*```` is a paragraph line and `# heading` is a real ATX heading. A link around the block
produces link text that spans a block, which CommonMark does not parse as a link. Splitting
the ancestor, rather than unwrapping it, keeps `Note:` bold in
`<strong>Note: <code>a<br>b</code></strong>`.

The split SHALL copy the ancestor's attributes onto both halves, SHALL drop a half that
holds no tag and no non-blank text, and SHALL drop the whitespace of each half that touches
the cut. The hoist SHALL run inside the deep-safe worker with the rest of the promotion, so a
deeply nested page (issue #40) is converted, not failed, by the new code path.

#### Scenario: An emphasized multi-line code element becomes a bare fence

- **WHEN** `html_to_markdown('<p><em><code>a<br># heading</code></em></p>')` is called
- **THEN** the output is exactly `` ```\na\n# heading\n``` ``
- **AND** no line of the output starts with `*` or with `# `

#### Scenario: A bold code element with a language class keeps its infostring

- **WHEN** `html_to_markdown('<p><strong><code class="bash">a<br># heading</code></strong></p>')` is called
- **THEN** the output is exactly `` ```bash\na\n# heading\n``` ``

#### Scenario: A link around a code block becomes two links

- **WHEN** `html_to_markdown('<p><a href="http://x">See <code>a<br>b</code> now</a></p>')` is called
- **THEN** the output is exactly `` [See](http://x)\n\n```\na\nb\n```\n\n[now](http://x) ``
- **AND** both halves carry the same `href`

#### Scenario: Sibling text keeps its formatting on both sides

- **WHEN** `html_to_markdown('<p><strong>Note: <code>a<br>b</code> done</strong></p>')` is called
- **THEN** the output is exactly `` **Note:**\n\n```\na\nb\n```\n\n**done** ``
- **AND** no line of the output ends with a space or starts with a space

#### Scenario: Nested marked ancestors are all split

- **WHEN** `html_to_markdown('<p><a href="http://x"><em>See <code>a<br>b</code> now</em></a></p>')` is called
- **THEN** the output is exactly `` [*See*](http://x)\n\n```\na\nb\n```\n\n[*now*](http://x) ``

#### Scenario: A keyboard tag around a code block becomes a bare fence

- **WHEN** `html_to_markdown('<p><kbd><code>a<br># heading</code></kbd></p>')` is called
- **THEN** the output is exactly `` ```\na\n# heading\n``` ``

#### Scenario: A half with no content is dropped

- **WHEN** `_promote_block_code('<p><em> <code>a<br>b</code> </em></p>')` is called
- **THEN** the result holds no `<em>` element
- **AND** the `<pre>` element's parent is the `<p>`
- **AND** `_promote_block_code('<p><em><!-- c --><code>a<br>b</code></em></p>')` also holds no `<em>` element

#### Scenario: A half that holds only a tag is kept

- **WHEN** `_promote_block_code('<p><a href="http://x"><img src="i.png"/><code>a<br>b</code></a></p>')` is called
- **THEN** the result holds exactly one `<a>` element, it contains the `<img>`, and it precedes the `<pre>`

#### Scenario: Whitespace behind a void tag at the cut is kept

- **WHEN** `html_to_markdown('<p><em>x <img src="i.png"/><code>a<br>b</code></em></p>')` is called
- **THEN** the output is exactly `` *x ![](i.png)*\n\n```\na\nb\n``` ``
- **AND** the space after `x` is kept, because the `<img>` at the cut has no text to trim

#### Scenario: Two promoted blocks in one ancestor are both hoisted

- **WHEN** `html_to_markdown('<p><em>x <code>a<br>b</code> y <code>c<br>d</code> z</em></p>')` is called
- **THEN** the output is exactly `` *x*\n\n```\na\nb\n```\n\n*y*\n\n```\nc\nd\n```\n\n*z* ``

#### Scenario: Inline single-line code inside emphasis stays inline

- **WHEN** `html_to_markdown('<p><em>Add <code>--gpus=1</code>.</em></p>')` is called
- **THEN** the output is exactly `*Add `--gpus=1`.*`, the same string the conversion produced before this change
- **AND** the output contains no fence

#### Scenario: The existing fence guards are unchanged

- **WHEN** `html_to_markdown('<pre class="bash"><code>echo hi</code></pre>')` is called
- **THEN** the output is exactly `` ```\necho hi\n``` ``
- **AND** `html_to_markdown('<p><code class="bash">#!/bin/bash<br />\n# comment<br />\necho hi</code></p>')` is exactly `` ```bash\n#!/bin/bash\n# comment\necho hi\n``` ``
- **AND** `html_to_markdown('<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>')` is exactly `# Title\n\nAdd `--gpus=1`.`

#### Scenario: A deeply nested page still converts

- **WHEN** a 2000-level nested `<div>` tree is processed
- **THEN** the resource is converted to Markdown with suffix `md`
- **AND** the process-global recursion limit equals its value before the call
