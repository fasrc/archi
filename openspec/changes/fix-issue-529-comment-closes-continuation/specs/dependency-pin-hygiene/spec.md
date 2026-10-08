## ADDED Requirements

### Requirement: The base-image guard SHALL insert pip's separator when a whole-line comment closes an open continuation
When a continuation buffer is open and the next physical line matches pip's comment rule (`COMMENT_RE`, same as `_COMMENT`), the guard SHALL join it as `buffered + " " + line`, as pip 26.1.2's `join_lines` does in its `COMMENT_RE` branch. A non-comment continuation SHALL stay a plain concatenation with no separator. Without the separator the comment is not cut, the anchored pin pattern fails, and the protected package before the comment silently reads as absent (measured 2026-10-08 at `5564e016`: `_parse_pins` returned only `torch` for the repro below).

#### Scenario: A comment closing a continuation keeps the pin before it
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0\`, then `# note \`, then `torch==2.7.0`
- **THEN** `_parse_pins` returns `{"vllm": "0.9.0", "torch": "2.7.0"}`
- **AND** `_joined_lines` yields `vllm==0.9.0 # note \` as the first logical line, matching pip's `join_lines`

#### Scenario: A non-comment continuation still joins with no separator
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0\` followed by `--hash=sha256:aa`
- **THEN** `_joined_lines` yields `vllm==0.9.0--hash=sha256:aa`, the same text pip's `join_lines` yields

#### Scenario: The monitored requirement files parse as before
- **WHEN** the guard reads the five monitored requirement files after the change
- **THEN** each reports 0 opaque lines
- **AND** the pin counts are 1, 5, 105, 106 and 110, unchanged from `origin/dev`
