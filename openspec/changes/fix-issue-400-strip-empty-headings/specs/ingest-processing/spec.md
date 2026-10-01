## ADDED Requirements

### Requirement: Conversion drops empty headings
The system SHALL remove every `h1`–`h6` element whose stripped text is empty and that has no `img` or `code` descendant before it converts HTML to Markdown, so that no bare ATX heading marker reaches the persisted Markdown.

#### Scenario: Empty heading is dropped
- **WHEN** `html_to_markdown("<h3></h3><p>x</p>")` is called
- **THEN** the result has no line that starts with `#`, and it contains `x`

#### Scenario: Whitespace-only and empty-span headings are dropped
- **WHEN** `html_to_markdown` converts `<h2>   </h2><p>x</p>`, `<h2>&nbsp;</h2><p>x</p>`, or `<h1><span></span></h1><p>x</p>`
- **THEN** each result has no line that starts with `#`

#### Scenario: A heading with content is kept
- **WHEN** `html_to_markdown` converts `<h3>Title</h3>`, `<h3><code>foo</code></h3>`, or `<h3><img src="a.png" alt="A"></h3>`
- **THEN** the results are `### Title`, ``### `foo` ``, and `### A`, the same as before this change

#### Scenario: Ingest and drift check agree
- **WHEN** `HtmlToMarkdownProcessor` converts an `html` resource that contains `<h3></h3>`
- **THEN** the persisted Markdown has no bare `###` line, because the processor calls the same `html_to_markdown` function as the drift check
