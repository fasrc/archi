## ADDED Requirements

### Requirement: A self-link without a URI scheme converts to explicit link syntax

When an anchor's link text equals its `href`, the anchor has no `title`, and the `href` has no URI scheme, the conversion SHALL write `[text](href)` with the surrounding whitespace kept, and never the `<href>` autolink shortcut.

An `href` with a URI scheme (for example `https:`, `http:`, or `mailto:`) SHALL keep the `<href>` autolink. An anchor inside `code`, `kbd`, `samp`, or `pre` SHALL convert exactly as it did before this change.

#### Scenario: A root-relative self-link becomes a Markdown link

- **WHEN** `html_to_markdown('<p><a href="/docs">/docs</a></p>')` is called
- **THEN** the output is exactly `[/docs](/docs)`

#### Scenario: A fragment self-link becomes a Markdown link

- **WHEN** `html_to_markdown('<p><a href="#sec">#sec</a></p>')` is called
- **THEN** the output is exactly `[#sec](#sec)`

#### Scenario: A path-relative self-link becomes a Markdown link

- **WHEN** `html_to_markdown('<p><a href="docs/page.html">docs/page.html</a></p>')` is called
- **THEN** the output is exactly `[docs/page.html](docs/page.html)`

#### Scenario: Whitespace around a self-link is kept

- **WHEN** `html_to_markdown('<p>see<a href="/docs"> /docs </a>now</p>')` is called
- **THEN** the output is exactly `see [/docs](/docs) now`

#### Scenario: The link text keeps the converter's escapes

- **WHEN** `html_to_markdown('<p><a href="/a_b">/a_b</a></p>')` is called
- **THEN** the output is exactly `[/a\_b](/a_b)`

#### Scenario: An absolute self-link stays an autolink

- **WHEN** `html_to_markdown('<p><a href="https://x/y">https://x/y</a></p>')` is called
- **THEN** the output is exactly `<https://x/y>`
- **AND** `html_to_markdown('<p><a href="mailto:a@b">mailto:a@b</a></p>')` is exactly `<mailto:a@b>`

#### Scenario: A link with other text is unchanged

- **WHEN** `html_to_markdown('<p><a href="/docs">Docs</a></p>')` is called
- **THEN** the output is exactly `[Docs](/docs)`

#### Scenario: A titled self-link is unchanged

- **WHEN** `html_to_markdown('<p><a href="/docs" title="T">/docs</a></p>')` is called
- **THEN** the output is exactly `[/docs](/docs "T")`

#### Scenario: A link inside code is unchanged

- **WHEN** `html_to_markdown('<p><code><a href="/x">&lt;/x&gt;</a></code></p>')` is called
- **THEN** the output is exactly `` `</x>` ``

#### Scenario: The link that the hoist keeps uses the same rule

- **WHEN** `html_to_markdown('<p><a href="/a_b"><code>a<br>b</code></a></p>')` is called
- **THEN** the output is exactly `` [/a\_b](/a_b)\n\n```\na\nb\n``` ``
