## ADDED Requirements

### Requirement: Content after a nested list starts on its own line

The HTML-to-Markdown conversion SHALL emit a line break between a list nested inside a list item and the content that follows it inside the same item, unless that content is a block element whose Markdown already starts on a new line, so a closing code fence or a nested item's last line is never joined to the text after it.

`markdownify` 1.2.2 returns `'\n' + text.rstrip()` for a list whose parent is a list item
and strips the leading whitespace of the text node that follows a block element, so the
content after a nested list is glued onto the nested list's last line. When the last nested
item ends in a code block, the glued line is the closing fence, and a CommonMark reader
keeps the fence open until the next opener. The persisted text is what the chat shows and
what the golden set scores against.

The break SHALL be added for ordered and unordered lists alike, SHALL treat a comment, a
doctype, and whitespace-only text as absent when it looks for the following content, and
SHALL leave a nested list unchanged when it ends its item, when it is empty, or when a
paragraph, code block, heading, list, or `<br>` follows it.

#### Scenario: Text after a nested list that ends in a code block starts on its own line

- **WHEN** `html_to_markdown('<ul><li>Outer item<ul><li>Inner: <pre>x = 1</pre></li></ul>After the nested list.</li></ul>')` is called
- **THEN** the output contains a line that is exactly four spaces and three backticks
- **AND** `After the nested list.` appears on a later line than that closing fence
- **AND** the substring ```` ```After ```` does not occur

#### Scenario: Prose after a nested list starts on its own line

- **WHEN** `html_to_markdown('<ul><li>Outer item<ul><li>Inner ends in prose</li></ul>After the nested list.</li></ul>')` is called
- **THEN** `Inner ends in prose` and `After the nested list.` are on separate lines
- **AND** the substring `proseAfter` does not occur

#### Scenario: A sibling item that the source never closed starts on its own line

- **WHEN** `html_to_markdown('<ul><li>A<ul><li><pre>docker rm alpine</pre></li></ul><li>Configure a bundle</li></li></ul>')` is called
- **THEN** the closing fence and `Configure a bundle` are on separate lines
- **AND** the substring ```` ```* ```` does not occur

#### Scenario: An ordered list gets the same break

- **WHEN** `html_to_markdown('<ol><li>Outer<ol><li>Inner</li></ol>After.</li></ol>')` is called
- **THEN** the output is exactly `1. Outer\n   1. Inner\n   After.`

#### Scenario: An inline element after a nested list starts on its own line

- **WHEN** `html_to_markdown('<ul><li>Outer<ul><li>Inner</li></ul><a href="http://x">link</a> tail</li></ul>')` is called
- **THEN** the output is exactly `* Outer\n  + Inner\n  [link](http://x) tail`

#### Scenario: A comment between the nested list and the text is not content

- **WHEN** `html_to_markdown('<ul><li>Outer<ul><li>Inner</li></ul><!-- c -->tail</li></ul>')` is called
- **THEN** the output is exactly `* Outer\n  + Inner\n  tail`
- **AND** `html_to_markdown('<ul><li>Outer<ul><li>Inner</li></ul><!-- c --></li></ul>')` is exactly `* Outer\n  + Inner`, the same string the conversion produced before this change

#### Scenario: A nested list that ends its item is unchanged

- **WHEN** `html_to_markdown('<ul><li>Outer<ul><li>Inner</li></ul></li><li>Next outer</li></ul>')` is called
- **THEN** the output is exactly `* Outer\n  + Inner\n* Next outer`, the same string the conversion produced before this change
- **AND** the same holds when whitespace-only text sits between the nested list and the closing item tag

#### Scenario: A block element after a nested list is unchanged

- **WHEN** a paragraph, a code block, a heading, another list, or a `<br>` follows the nested list inside the same item
- **THEN** the output is byte-identical to the string the conversion produced before this change: `* Outer\n  + Inner\n\n  Para` for `<p>Para</p>`, ``* Outer\n  + Inner\n\n  ```\n  code\n  ``` `` for `<pre>code</pre>`, `* Outer\n  + Inner\n\n  ### Head` for `<h3>Head</h3>`, `* Outer\n  + Inner\n  + Second` for `<ul><li>Second</li></ul>`, and `* Outer\n  + Inner  \n  tail` for `<br>tail`

#### Scenario: An empty nested list and a top-level list are unchanged

- **WHEN** `html_to_markdown('<ul><li>Outer<ul></ul>tail</li></ul>')` is called
- **THEN** the output is exactly `* Outer\n  tail`, the same string the conversion produced before this change
- **AND** `html_to_markdown('<ul><li>a</li></ul>tail text')` is exactly `* a\n\ntail text`, the same string the conversion produced before this change

#### Scenario: The processor persists the separated form

- **WHEN** `HtmlToMarkdownProcessor` processes an `html` resource whose content is the nested list of the first scenario
- **THEN** the resource's suffix becomes `md` and its persisted content equals `html_to_markdown()` of the same HTML
- **AND** the persisted content does not contain the substring ```` ```After ````

#### Scenario: A deeply nested page still converts

- **WHEN** a 2000-level nested `<div>` tree is processed
- **THEN** the resource is converted to Markdown with suffix `md`
- **AND** the process-global recursion limit equals its value before the call
