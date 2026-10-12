## ADDED Requirements

### Requirement: Link destinations round-trip through CommonMark
The HTML-to-Markdown conversion SHALL write each link destination in a form that CommonMark 0.31 parses back to the original `href`, and it SHALL write a link whose `href` has no whitespace, no control character, no backslash before ASCII punctuation, no `&` that leads a character reference, and only balanced parentheses byte-for-byte as before.

When the bare destination is not valid (the `href` starts with `<`, has whitespace or a control character, has a `\` before ASCII punctuation or an `&` that leads a character reference, or its parentheses are not balanced), the conversion writes the destination in angle brackets: it doubles each literal `\` first, then puts a backslash before each `<` and `>`, and before each `&` that leads a character reference (`&name;`, `&#NNN;`, `&#xHHH;`), in the `href`. When the conversion writes an autolink and the `href` has whitespace, a control character, `<`, or `>`, it writes an explicit link with the `href` as the text instead, escaping any `\`, `[`, `]`, `<`, `>`, backtick, `*`, `_`, and any `&` that leads a character reference in that text so the text reads back literally. The link that the hoist keeps (issue #430) obeys the same rule. When a link's destination has already been written once inside its own text (for example, an image whose `src` matches the link's `href`), the escaping targets the link's own, outermost destination, not the first matching occurrence. A link inside a `_noformat` context (a code span or block) is left exactly as the base converter renders it.

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

#### Scenario: A control character forces an explicit link
- **WHEN** `html_to_markdown('<p><a href="/a\x01b">/a\x01b</a></p>')` runs
- **THEN** the result is `[/a\x01b](</a\x01b>)`

#### Scenario: A destination that starts with an angle bracket
- **WHEN** `html_to_markdown('<p><a href="&lt;foo">Docs</a></p>')` runs
- **THEN** the result is `[Docs](<\<foo>)`

#### Scenario: A literal backslash is preserved
- **WHEN** `_link_destination('/a b\)c')` runs
- **THEN** the result is `</a b\\)c>`

#### Scenario: A character reference is escaped
- **WHEN** `_link_destination('/a b&amp;')` runs
- **THEN** the result is `</a b\&amp;>`

#### Scenario: An image shares the link's href
- **WHEN** `html_to_markdown('<p><a href="/a b"><img src="/a b" alt="pic"></a></p>')` runs
- **THEN** the result is `[![pic](/a b)](</a b>)`

#### Scenario: An autolink label with an unmatched bracket
- **WHEN** `html_to_markdown('<p><a href="https://x/a ] b">https://x/a ] b</a></p>')` runs
- **THEN** the result is `[https://x/a \] b](<https://x/a ] b>)`

#### Scenario: A link inside a code block is left untouched
- **WHEN** `html_to_markdown('<pre><a href="https://x/a b">&lt;https://x/a b&gt;</a></pre>')` runs
- **THEN** the result is the fenced block containing `<https://x/a b>`

#### Scenario: A title that repeats the href
- **WHEN** `html_to_markdown('<p><a href="/a b" title="x](/a b">Docs</a></p>')` runs
- **THEN** the result is `[Docs](</a b> "x](/a b")`

#### Scenario: An autolink label with Markdown syntax
- **WHEN** `html_to_markdown('<p><a href="https://x/a `c` b">https://x/a `c` b</a></p>')` runs
- **THEN** the result is ``[https://x/a \`c\` b](<https://x/a `c` b>)``

#### Scenario: A literal backslash before punctuation
- **WHEN** `html_to_markdown('<p><a href="/a\\)b">Docs</a></p>')` runs
- **THEN** the result is `[Docs](</a\\)b>)`
