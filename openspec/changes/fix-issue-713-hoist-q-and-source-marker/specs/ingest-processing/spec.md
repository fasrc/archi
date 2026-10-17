## ADDED Requirements

### Requirement: A hoisted anchor keeps one link when a tag writes link text with no text in it
When the hoist of a promoted code block splits an `a` ancestor with a non-empty `href`, the HTML-to-Markdown conversion SHALL count a `<q>`, `<h1>`–`<h6>`, `<td>`, `<th>`, or `<tr>` descendant as link text even when it holds no text, as it already counts `<img>`, `<hr>`, and `<video>`, so that the anchor renders exactly one link to its `href`, except that it SHALL NOT count these newly counted tags when the anchor is inside a `<pre>`, `<code>`, `<kbd>`, or `<samp>` and SHALL NOT count a heading when the anchor is inside a heading, `<td>`, or `<th>`, and it SHALL NOT change the output for an anchor that holds none of these newly counted tags.

markdownify 1.2.2 writes characters for each of these tags when the tag is empty (`""`, one to six `#`, `|`), so a half that holds one renders a link. In the scenarios below, `A` is the Python string ``'```\na\nb\n```'`` and `C` is the Python string ``'```\nc\nd\n```'``.

#### Scenario: An empty quotation after two blocks gives one link
- **WHEN** `html_to_markdown('<p><a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><q></q></a></p>')` runs
- **THEN** the result is `A + "\n\n" + C + '\n\n[""](https://x/y)'`

#### Scenario: The issue's first probe input gives one link
- **WHEN** `html_to_markdown('<a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><q></q></a>')` runs
- **THEN** the result is `A + "\n\n" + C + '\n\n[""](https://x/y)'` and it holds `https://x/y` exactly once

#### Scenario: An empty quotation before a block is kept as the link
- **WHEN** `html_to_markdown('<p><a href="https://x/y"><q></q><code>a<br>b</code></a></p>')` runs
- **THEN** the result is `'[""](https://x/y)\n\n' + A`

#### Scenario: An empty heading after two blocks gives one link
- **WHEN** `html_to_markdown('<p><a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><h1></h1></a></p>')` runs
- **THEN** the result is `A + "\n\n" + C + "\n\n[#](https://x/y)"`, and `<h2>` to `<h6>` give the same result with two to six `#`

#### Scenario: An empty table cell after two blocks gives one link
- **WHEN** `html_to_markdown('<p><a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><td></td></a></p>')` runs
- **THEN** the result is `A + "\n\n" + C + "\n\n [|](https://x/y)"`, and `<th></th>` gives the same result

#### Scenario: An empty table row after two blocks gives one link
- **WHEN** `html_to_markdown('<p><a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><tr></tr></a></p>')` runs
- **THEN** the result is `A + "\n\n" + C + "\n\n[|\n|  |](https://x/y)"`

#### Scenario: An empty heading before a block is kept as the link
- **WHEN** `html_to_markdown('<p><a href="https://x/y"><h1></h1><code>a<br>b</code></a></p>')` runs
- **THEN** the result is `"[#](https://x/y)\n\n" + A`

#### Scenario: An empty span after two blocks is still not link text
- **WHEN** `html_to_markdown('<p><a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><span></span></a></p>')` runs
- **THEN** the result is `"<https://x/y>\n\n" + A + "\n\n" + C`, the same as before this change

#### Scenario: A lone block in a link is unchanged
- **WHEN** `html_to_markdown('<p><a href="https://x/y"><code>a<br>b</code></a></p>')` runs
- **THEN** the result is `"<https://x/y>\n\n" + A`, the same as before this change

#### Scenario: An empty quotation under a keyboard tag keeps the target
- **WHEN** `html_to_markdown('<kbd><a href="https://x/y"><code>a<br>b</code><q></q></a></kbd>')` runs
- **THEN** the result is ``"`https://x/y`\n\n" + A``, the same as before this change, and `<samp>` or any other newly counted tag in place of `<q>` gives the same result

#### Scenario: An empty heading in a table cell keeps the target
- **WHEN** `html_to_markdown('<td><a href="https://x/y"><code>a<br>b</code><h1></h1></a></td>')` runs
- **THEN** the result is ``" <https://x/y>  ``` a b ``` |"``, the same as before this change, and `<th>` or `<h2>` in place of `<td>` gives the same link

#### Scenario: An empty quotation in a table cell gives one link
- **WHEN** `html_to_markdown('<td><a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><q></q></a></td>')` runs
- **THEN** the result is ``' ``` a b ```  ``` c d ```  [""](https://x/y) |'``

### Requirement: The hoist ignores its marker attributes in the source HTML
The HTML-to-Markdown conversion SHALL remove the `data-archi-link-shown` and `data-archi-promoted` attributes from every tag of the source HTML before it promotes or hoists any code block, so that only the conversion itself sets them, and it SHALL still mark each block that it promotes with `data-archi-promoted`.

#### Scenario: A source link-shown attribute does not hide the link target
- **WHEN** `html_to_markdown('<p><a data-archi-link-shown href="https://x/y"><code>a<br>b</code></a></p>')` runs
- **THEN** the result is `"<https://x/y>\n\n" + A`

#### Scenario: The issue's second probe input keeps the link
- **WHEN** `html_to_markdown('<a data-archi-link-shown href="https://x/y"><code>a<br>b</code></a>')` runs
- **THEN** the result is `"<https://x/y>\n\n" + A`

#### Scenario: An upper-case source attribute name is removed too
- **WHEN** `html_to_markdown('<p><a DATA-ARCHI-LINK-SHOWN href="https://x/y"><code>a<br>b</code></a></p>')` runs
- **THEN** the result is `"<https://x/y>\n\n" + A`

#### Scenario: A source promoted attribute does not label a native block
- **WHEN** `html_to_markdown('<pre data-archi-promoted class="bash">ls</pre>')` runs
- **THEN** the result is ``'```\nls\n```'``, the same as for `<pre class="bash">ls</pre>`

#### Scenario: The promote step drops both source markers
- **WHEN** `_promote_block_code('<p><a data-archi-link-shown="" href="x">t</a><pre data-archi-promoted class="bash">ls</pre></p>')` runs
- **THEN** the result is `'<p><a href="x">t</a><pre class="bash">ls</pre></p>'`

#### Scenario: A promoted block still carries the marker
- **WHEN** `_promote_block_code('<p><code class="bash">a<br>b</code></p>')` runs
- **THEN** the result is `'<p><pre class="bash" data-archi-promoted=""><code class="bash">a\nb</code></pre></p>'`
