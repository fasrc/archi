## ADDED Requirements

### Requirement: Link text with an unbalanced bracket round-trips through CommonMark
The HTML-to-Markdown conversion SHALL put a backslash before each `[` or `]` in the text of an explicit link that is unbalanced, not already backslash-escaped, and not inside a code span, an autolink, raw inline HTML, or a nested image destination or title, it SHALL escape both brackets of a nested link, it SHALL double a lone backslash at the end of that text, and it SHALL leave all other link text byte-for-byte as before.

This applies to every explicit link that `convert_a` writes (`[text](dest)`, with or without a title), including a self-link whose text no longer equals its `href` and a titled self-link. A `<href>` autolink is unchanged. The scheme-less self-link label keeps its own escape (`_escape_link_label`). A link inside a `_noformat` context (a code span or block) is left exactly as the base converter renders it. The link destination logic of issue #605 is unchanged.

#### Scenario: An unmatched closing bracket in link text
- **WHEN** `html_to_markdown('<p><a href="https://x/y">a ] b</a></p>')` runs
- **THEN** the result is the Python string `'[a \\] b](https://x/y)'`

#### Scenario: An unmatched opening bracket in link text
- **WHEN** `html_to_markdown('<p><a href="x">a [ b</a></p>')` runs
- **THEN** the result is the Python string `'[a \\[ b](x)'`

#### Scenario: A self-link whose text no longer equals its href
- **WHEN** `html_to_markdown('<a href="https://x/a  ]b">https://x/a  ]b</a>')` runs
- **THEN** the result is the Python string `'[https://x/a \\]b](<https://x/a  ]b>)'`

#### Scenario: A titled self-link
- **WHEN** `html_to_markdown('<a href="https://x/a ] b" title="T">https://x/a ] b</a>')` runs
- **THEN** the result is the Python string `'[https://x/a \\] b](<https://x/a ] b> "T")'`

#### Scenario: Balanced brackets do not change
- **WHEN** `html_to_markdown('<p><a href="x">a [b] c</a></p>')` runs
- **THEN** the result is `[a [b] c](x)`

#### Scenario: A nested image does not change
- **WHEN** `html_to_markdown('<p><a href="/b"><img src="/a" alt="pic"></a></p>')` runs
- **THEN** the result is `[![pic](/a)](/b)`

#### Scenario: A bracket inside a code span does not change
- **WHEN** `html_to_markdown('<p><a href="x"><code>a]b</code></a></p>')` runs
- **THEN** the result is ``[`a]b`](x)``

#### Scenario: An already-escaped bracket does not change
- **WHEN** `html_to_markdown('<p><a href="x">a \\] b</a></p>')` runs (the HTML holds one backslash)
- **THEN** the result is the Python string `'[a \\] b](x)'`

#### Scenario: A bracket inside raw inline HTML does not count
- **WHEN** `html_to_markdown('<a href="x">&lt;span title="["&gt;x&lt;/span&gt;]</a>')` runs
- **THEN** the result is the Python string `'[<span title="[">x</span>\\]](x)'`

#### Scenario: A bracket inside an autolink in link text does not change
- **WHEN** `html_to_markdown('<a href="x">&lt;https://x/a]b&gt;</a>')` runs
- **THEN** the result is `[<https://x/a]b>](x)`

#### Scenario: A backtick in a nested image destination opens no code span
- **WHEN** `html_to_markdown('<a href="x"><img src="a&#96;" alt="pic"> tail ] &#96;</a>')` runs
- **THEN** the result is the Python string ``'[![pic](a`) tail \\] `](x)'``

#### Scenario: Link syntax in link text is written as text
- **WHEN** `html_to_markdown('<a href="u">[a](b) x</a>')` runs
- **THEN** the result is the Python string `'[\\[a\\](b) x](u)'`

#### Scenario: A terminal backslash in link text is doubled
- **WHEN** `html_to_markdown('<a href="x">[\\</a>')` runs (the HTML holds one backslash)
- **THEN** the result is the Python string `'[\\[\\\\](x)'`

#### Scenario: An anchor without an href is not escaped
- **WHEN** `html_to_markdown('<p><a>a ] b</a></p>')` runs
- **THEN** the result is `a ] b`

#### Scenario: An escaped label equal to the href keeps the HTML text
- **WHEN** `html_to_markdown('<a href="a\\]">a]</a>')` runs (the HTML holds one backslash)
- **THEN** the result is the Python string `'[a\\]](<a\\\\]>)'`

#### Scenario: An autolink with a bracket does not change
- **WHEN** `html_to_markdown('<p><a href="https://x/a]b">https://x/a]b</a></p>')` runs
- **THEN** the result is `<https://x/a]b>`
