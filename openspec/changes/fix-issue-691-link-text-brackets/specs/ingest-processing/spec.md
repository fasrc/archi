## ADDED Requirements

### Requirement: Link text with an unbalanced bracket round-trips through CommonMark
The HTML-to-Markdown conversion SHALL put a backslash before each `[` or `]` in the text of an explicit link that is unbalanced, not already backslash-escaped, and not inside a code span, and it SHALL leave all other link text byte-for-byte as before.

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

#### Scenario: An autolink with a bracket does not change
- **WHEN** `html_to_markdown('<p><a href="https://x/a]b">https://x/a]b</a></p>')` runs
- **THEN** the result is `<https://x/a]b>`
