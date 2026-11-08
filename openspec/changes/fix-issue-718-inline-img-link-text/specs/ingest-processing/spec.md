## ADDED Requirements

### Requirement: A split link in a heading or table cell counts an image only by its alt text and a video only by its text
When the hoist of a promoted code block splits an `a` ancestor with a non-empty `href` that is inside a heading, `<td>`, or `<th>` and not inside a `<pre>`, `<code>`, `<kbd>`, or `<samp>`, the HTML-to-Markdown conversion SHALL count an `<img>` as link text only when its `alt` attribute holds non-whitespace text and SHALL count a `<video>` only through the text that it holds, so that an anchor whose halves write no text keeps its target as one synthesized link, and it SHALL NOT change the output for an `<img>` with non-blank `alt`, for an `<hr>`, for a `<video>` that holds text, or for an anchor that is not inside a heading, `<td>`, or `<th>`.

markdownify 1.2.2 writes only the `alt` text of an `<img>` and only the child text of a `<video>` in a heading or table cell (its `_inline` context), so these tags write nothing there when that text is blank. In the scenarios below, `A` is the Python string ``'```\na\nb\n```'``.

#### Scenario: An image without alt text after a block in a table cell keeps the target
- **WHEN** `html_to_markdown('<td><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></td>')` runs
- **THEN** the result is ``" <https://x/y>  ``` a b ``` |"``

#### Scenario: An image with blank alt text in a table cell keeps the target
- **WHEN** `html_to_markdown('<td><a href="https://x/y"><code>a<br>b</code><img src="i.png" alt=" "></a></td>')` runs
- **THEN** the result is ``" <https://x/y>  ``` a b ``` |"``

#### Scenario: A video without text in a table cell keeps the target
- **WHEN** `html_to_markdown('<td><a href="https://x/y"><code>a<br>b</code><video src="v.mp4"></video></a></td>')` runs
- **THEN** the result is ``" <https://x/y>  ``` a b ``` |"``

#### Scenario: An image without alt text before a block in a header cell keeps the target
- **WHEN** `html_to_markdown('<th><a href="https://x/y"><img src="i.png"><code>a<br>b</code></a></th>')` runs
- **THEN** the result is ``" <https://x/y>  ``` a b ``` |"``

#### Scenario: An image without alt text in a heading keeps the target
- **WHEN** `html_to_markdown('<h2><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></h2>')` runs
- **THEN** the result is ``"## <https://x/y> ``` a b ```"``

#### Scenario: Two blocks and an image without alt text in a table keep one target
- **WHEN** `html_to_markdown('<table><tr><td><a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><img src="i.png"></a></td></tr></table>')` runs
- **THEN** the result is ``"|  |\n| --- |\n| <https://x/y>  ``` a b ```  ``` c d ``` |"``

#### Scenario: An image with alt text in a table cell is unchanged
- **WHEN** `html_to_markdown('<td><a href="https://x/y"><code>a<br>b</code><img src="i.png" alt="pic"></a></td>')` runs
- **THEN** the result is ``" ``` a b ```  [pic](https://x/y) |"``, the same as before this change

#### Scenario: A video with text in a table cell is unchanged
- **WHEN** `html_to_markdown('<td><a href="https://x/y"><code>a<br>b</code><video src="v.mp4">clip</video></a></td>')` runs
- **THEN** the result is ``" ``` a b ```  [clip](https://x/y) |"``, the same as before this change, and `<img src="p.png" alt="pic">` in place of `clip` gives `[pic](https://x/y)` in the same place

#### Scenario: A rule in a table cell is unchanged
- **WHEN** `html_to_markdown('<td><a href="https://x/y"><code>a<br>b</code><hr></a></td>')` runs
- **THEN** the result is ``" ``` a b ```  [---](https://x/y) |"``, the same as before this change

#### Scenario: An image or video without text outside a heading or cell is unchanged
- **WHEN** `html_to_markdown('<p><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></p>')` runs
- **THEN** the result is `A + "\n\n[![](i.png)](https://x/y)"`, the same as before this change, and `<video src="v.mp4"></video>` in place of the image gives `A + "\n\n[\\[\\](v.mp4)](https://x/y)"` (Python string literal), the same as before this change
