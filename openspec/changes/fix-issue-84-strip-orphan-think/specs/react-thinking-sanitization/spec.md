## ADDED Requirements

### Requirement: The ReAct agent strips orphan `</think>` closing tags from visible output

`BaseReActAgent._parse_thinking_content` SHALL remove chain-of-thought reasoning
demarcated by an **orphan** `</think>` closing tag — a closing tag with no matching
opening `<think>` — in addition to removing balanced `<think>…</think>` pairs. After
removing all balanced pairs, if any `</think>` remains, everything up to and including the
**last** remaining `</think>` MUST be treated as thinking, and only the text after it MUST
be returned as the visible content. The removed reasoning MUST be accumulated into the
returned thinking content so it is still captured, not silently discarded. The visible
content returned for any input MUST contain neither `<think>` nor `</think>`.

The contract covers `</think>`-demarcated reasoning only. Untagged residual model prose
outside any think tag is out of scope for this requirement.

#### Scenario: A single orphan closing tag is stripped

- **WHEN** `_parse_thinking_content` receives `"reasoning\n</think>\n\nAns"` (a closing tag
  with no matching open tag)
- **THEN** the returned visible content is `"Ans"`
- **AND** the visible content contains neither `<think>` nor `</think>`
- **AND** the returned thinking content includes the removed reasoning

#### Scenario: Multiple orphan closing tags are all stripped

- **WHEN** `_parse_thinking_content` receives
  `"t1\n</think>\n\nt2\n</think>\n\nt3\n</think>\n\nAns"` (the real-incident shape: three
  orphan closing tags)
- **THEN** the returned visible content is `"Ans"`
- **AND** the visible content contains neither `<think>` nor `</think>`

#### Scenario: Balanced pairs still behave as before (regression)

- **WHEN** `_parse_thinking_content` receives `"<think>r</think>\n\nAns"`
- **THEN** the returned visible content is `"Ans"`
- **AND** the returned thinking content contains `"r"`

#### Scenario: Text with no think tags is returned unchanged

- **WHEN** `_parse_thinking_content` receives `"Just an answer."`
- **THEN** the returned visible content is `"Just an answer."`
- **AND** the returned thinking content is empty

#### Scenario: Empty input yields empty output

- **WHEN** `_parse_thinking_content` receives `""`
- **THEN** it returns `("", "")`
