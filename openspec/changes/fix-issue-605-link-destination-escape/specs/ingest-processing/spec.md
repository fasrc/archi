## ADDED Requirements

### Requirement: Link destinations round-trip through CommonMark
The HTML-to-Markdown conversion SHALL write each link destination in a form that CommonMark 0.31 parses back to the original `href`, and it SHALL write a link whose `href` has no whitespace and only balanced parentheses byte-for-byte as before.

When the bare destination is not valid (the `href` has whitespace or a control character, or its unescaped parentheses are not balanced), the conversion writes the destination in angle brackets and puts a backslash before each `<` and `>` in the `href`. When the conversion writes an autolink and the `href` has whitespace, `<`, or `>`, it writes an explicit link with the `href` as the text instead. The link that the hoist keeps (issue #430) obeys the same rule.

#### Scenario: An unmatched closing parenthesis
- **WHEN** `html_to_markdown('<p><a href="/docs/foo)bar">Docs</a></p>')` runs
- **THEN** the result is `[Docs](</docs/foo)bar>)`

#### Scenario: An unmatched opening parenthesis
- **WHEN** `html_to_markdown('<p><a href="/docs/foo(bar">Docs</a></p>')` runs
- **THEN** the result is `[Docs](</docs/foo(bar>)`

#### Scenario: A space in the href
- **WHEN** `html_to_markdown('<p><a href="https://x/a b">Docs</a></p>')` runs
- **THEN** the result is `[Docs](<https://x/a b>)`

#### Scenario: Angle brackets inside an angle-bracket destination
- **WHEN** `html_to_markdown('<p><a href="/a b<c>">Docs</a></p>')` runs
- **THEN** the result is `[Docs](</a b\<c\>>)`

#### Scenario: A title is kept
- **WHEN** `html_to_markdown('<p><a href="/a(b" title="T">Docs</a></p>')` runs
- **THEN** the result is `[Docs](</a(b> "T")`

#### Scenario: An autolink that cannot hold a space
- **WHEN** `html_to_markdown('<p><a href="https://x/a b">https://x/a b</a></p>')` runs
- **THEN** the result is `[https://x/a b](<https://x/a b>)`

#### Scenario: The kept link around hoisted code
- **WHEN** `html_to_markdown('<p><a href="/docs/foo)bar"><code>a<br>b</code></a></p>')` runs
- **THEN** the result is the Python string `'[/docs/foo)bar](</docs/foo)bar>)'` followed by `'\n\n'` and the fenced block `a`, `b`

#### Scenario: Balanced parentheses do not change
- **WHEN** `html_to_markdown('<p><a href="/w/Foo_(bar)">Foo</a></p>')` runs
- **THEN** the result is `[Foo](/w/Foo_(bar))`

#### Scenario: A plain URL does not change
- **WHEN** `html_to_markdown('<p><a href="https://x/y">Docs</a></p>')` runs
- **THEN** the result is `[Docs](https://x/y)`
