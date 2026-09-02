## ADDED Requirements

### Requirement: Multi-line bare code converts to a fenced block

The HTML-to-Markdown conversion SHALL emit a fenced code block for a `<code>` element that has no `<pre>` ancestor and contains at least one `<br>`, with each `<br>` rendered as a line break inside the fence, so that no line of the element's text can parse as a Markdown heading.

`markdownify` 1.2.2 converts such an element to a single-backtick span whose `<br>` children
become two-space hard breaks. CommonMark parses blocks before inlines, so an inline span
cannot cross a line boundary, and every line that starts with `# ` becomes an ATX heading.
On `/kb/helmod-faq` this turns one 20-line shell script into 11 level-1 headings and 0 code
blocks. The persisted text is what the chat shows and what the golden set scores against.

The normalization SHALL run inside the deep-safe worker that already guards the conversion,
so a deeply nested page (issue #40) is converted, not failed, by the new code path.

#### Scenario: A multi-line bare code element with a language class becomes a fenced block

- **WHEN** `html_to_markdown('<p><code class="bash">#!/bin/bash<br># comment<br>echo hi</code></p>')` is called
- **THEN** the output opens a fence with the infostring `bash` and closes it
- **AND** the three lines appear in order with no trailing two-space hard break
- **AND** no line in the output both starts with `#` and ends with two spaces

#### Scenario: A single-line inline code element stays inline

- **WHEN** `html_to_markdown('<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>')` is called
- **THEN** the output is exactly `# Title\n\nAdd `--gpus=1`.`, the same string the conversion produced before this change
- **AND** the output contains no fence

#### Scenario: An existing pre block is byte-identical

- **WHEN** `html_to_markdown('<pre><code>#!/bin/bash\n# c\necho hi</code></pre>')` is called
- **THEN** the output is exactly `` ```\n#!/bin/bash\n# c\necho hi\n``` ``, the same string the conversion produced before this change

#### Scenario: The processor persists the fenced form

- **WHEN** `HtmlToMarkdownProcessor` processes an `html` resource whose content is a multi-line bare `<code>` element
- **THEN** the resource's suffix becomes `md` and its persisted content equals `html_to_markdown()` of the same HTML
- **AND** the persisted content contains the fence

#### Scenario: A deeply nested page still converts

- **WHEN** a 2000-level nested `<div>` tree is processed
- **THEN** the resource is converted to Markdown with suffix `md`
- **AND** the process-global recursion limit equals its value before the call

### Requirement: The fence language comes from an allowlist

The conversion SHALL set the fence infostring to a class of the promoted element only when that class, compared lowercase, is one of `bash`, `sh`, `spec`, `lua`, `python`, `c`, `cpp`, `fortran`, `r`, `perl`, `json`, `yaml`, `text`, and SHALL emit a bare fence otherwise.

A page can carry any CSS class on a `<code>` element, such as `wp-block-code` or `hljs`. An
arbitrary class passed through as an infostring is wrong Markdown and leaks presentation
markup into the corpus.

#### Scenario: A non-language class emits a bare fence

- **WHEN** `html_to_markdown('<p><code class="wp-block-code">line1<br>line2</code></p>')` is called
- **THEN** the output opens with a bare fence and no infostring
- **AND** `wp-block-code` does not appear in the output

#### Scenario: The first allowlisted class wins and unknown classes yield none

- **WHEN** the promoted `<pre>` carries the classes `hljs bash`
- **THEN** the infostring is `bash`
- **AND** when the `<pre>` carries no class, or only classes outside the allowlist, the infostring is empty

#### Scenario: The comparison is case-insensitive

- **WHEN** the promoted `<pre>` carries the class `Bash`
- **THEN** the infostring is `bash`
