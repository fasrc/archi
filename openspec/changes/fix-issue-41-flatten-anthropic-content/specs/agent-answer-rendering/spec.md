## ADDED Requirements

### Requirement: Final message content is normalised to readable text across providers

The ReAct agent SHALL normalise a message's `.content` to a plain printable string
that is the same regardless of whether the underlying provider returns content as a
plain string or as a list of content-block dicts. When `.content` is a list, the
normaliser SHALL extract the textual payload of each block and MUST NOT emit a Python
`dict`/`repr` literal (e.g. `{'text': ...}` or `{'type': 'text', ...}`) into the
returned string.

#### Scenario: Anthropic-style content-block list flattens to its text

- **WHEN** a message's `.content` is `[{"type": "text", "text": "hello"}]`
- **THEN** the normalised content is `"hello"`
- **AND** the result contains no `{'text'` or `{'type': 'text'` substring

#### Scenario: Multiple text blocks are joined in order

- **WHEN** a message's `.content` is `[{"type": "text", "text": "hi"}, {"type": "text", "text": "there"}]`
- **THEN** the normalised content is `"hi there"`

#### Scenario: Plain string content is unchanged

- **WHEN** a message's `.content` is the plain string `"plain"`
- **THEN** the normalised content is `"plain"`

#### Scenario: Non-text blocks are dropped without error

- **WHEN** a message's `.content` mixes a text block with a non-text block, e.g. `[{"type": "text", "text": "answer"}, {"type": "tool_use", "name": "search"}]`
- **THEN** normalisation succeeds without raising
- **AND** the normalised content includes the text payload `"answer"`
- **AND** the result contains no dict-`repr` literal for the non-text block
