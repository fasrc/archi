## ADDED Requirements

### Requirement: An anchor that the hoist empties keeps its link

When the hoist of a promoted code block leaves an `a` ancestor with a non-empty `href` and no content in either half, the conversion SHALL replace that anchor with a link that holds only the `href`, placed directly before the promoted block, whose text is the anchor's stripped `title` or, when the title is absent or blank, the `href`.

An anchor that keeps content in either half, or that has no `href` or an empty `href`, SHALL
convert exactly as it did before this change.

#### Scenario: An untitled anchor around a code block keeps its target

- **WHEN** `html_to_markdown('<p><a href="https://x/y"><code>a<br>b</code></a></p>')` is called
- **THEN** the output is exactly `` <https://x/y>\n\n```\na\nb\n``` ``

#### Scenario: A titled anchor uses its title as the link text

- **WHEN** `html_to_markdown('<p><a href="https://x/y" title="Docs"><code>a<br>b</code></a></p>')` is called
- **THEN** the output is exactly `` [Docs](https://x/y)\n\n```\na\nb\n``` ``

#### Scenario: A blank title falls back to the href

- **WHEN** `html_to_markdown('<p><a href="https://x/y" title="  "><code>a<br>b</code></a></p>')` is called
- **THEN** the output is exactly `` <https://x/y>\n\n```\na\nb\n``` ``

#### Scenario: A nested emphasis is dropped and the link is kept

- **WHEN** `html_to_markdown('<p><a href="https://x/y"><em><code>a<br>b</code></em></a></p>')` is called
- **THEN** the output is exactly `` <https://x/y>\n\n```\na\nb\n``` ``
- **AND** `_promote_block_code` on the same input gives a tree with exactly one `<a>`, no `<em>`, the `<a>` directly before the `<pre>`, and the `<p>` as the `<pre>`'s parent

#### Scenario: Whitespace and comments around the block do not count as content

- **WHEN** `html_to_markdown('<p><a href="http://x"> <code>a<br>b</code> </a></p>')` is called
- **THEN** the output is exactly `` <http://x>\n\n```\na\nb\n``` ``
- **AND** `html_to_markdown('<p><a href="http://x"><!-- c --><code>a<br>b</code></a></p>')` is also exactly `` <http://x>\n\n```\na\nb\n``` ``

#### Scenario: Two blocks in one anchor give one link

- **WHEN** `html_to_markdown('<p><a href="http://x"><code>a<br>b</code><code>c<br>d</code></a></p>')` is called
- **THEN** the output is exactly `` <http://x>\n\n```\na\nb\n```\n\n```\nc\nd\n``` ``

#### Scenario: An outer marked ancestor wraps the kept link

- **WHEN** `html_to_markdown('<p><strong><a href="http://x"><code>a<br>b</code></a></strong></p>')` is called
- **THEN** the output is exactly `` **<http://x>**\n\n```\na\nb\n``` ``

#### Scenario: An anchor with no href is still dropped

- **WHEN** `html_to_markdown('<p><a><code>a<br>b</code></a></p>')` is called
- **THEN** the output is exactly `` ```\na\nb\n``` ``
- **AND** `html_to_markdown('<p><a href=""><code>a<br>b</code></a></p>')` is also exactly `` ```\na\nb\n``` ``

#### Scenario: An anchor with other content converts as before

- **WHEN** `html_to_markdown('<p><a href="http://x"><code>a<br>b</code> now</a></p>')` is called
- **THEN** the output is exactly `` ```\na\nb\n```\n\n[now](http://x) ``
- **AND** `html_to_markdown('<p><a href="http://x">See <code>a<br>b</code></a></p>')` is exactly `` [See](http://x)\n\n```\na\nb\n``` ``
- **AND** `html_to_markdown('<p><a href="http://x">See <code>a<br>b</code> now</a></p>')` is exactly `` [See](http://x)\n\n```\na\nb\n```\n\n[now](http://x) ``
