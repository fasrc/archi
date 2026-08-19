## ADDED Requirements

### Requirement: A /v1 chat completion presents exactly one source list

A `/v1/chat/completions` response with source documents SHALL contain exactly one source
list in its message content, built by `format_citations`. The chat wrapper's own appended
source list (`format_links_markdown` output) SHALL NOT appear in `/v1` message content. The
`final` stream event SHALL expose the bare answer (the pipeline answer without the wrapper's
appended source list) alongside the existing finalized `response` field, which SHALL remain
unchanged for existing consumers.

#### Scenario: Non-streaming response with sources has a single source section

- **WHEN** a non-streaming `/v1/chat/completions` request completes with source documents and
  retriever scores, and the `final` event's `response` already ends with the wrapper's
  appended source list
- **THEN** the returned message content contains exactly one `**Sources:**` block (the
  `format_citations` output)
- **AND** the wrapper's `Show all sources` block does not appear in the content

#### Scenario: Non-streaming response without sources has no source section

- **WHEN** a non-streaming `/v1/chat/completions` request completes with no source documents
- **THEN** the returned message content is the bare answer with no source list appended

#### Scenario: Empty answer with sources yields citations only

- **WHEN** a non-streaming `/v1/chat/completions` request completes with an empty bare answer
  but non-empty source documents
- **THEN** the returned message content is exactly the `format_citations` output, and the
  wrapper's appended source list does not reappear via fallback

#### Scenario: A missing pipeline answer is omitted from the final event, never defaulted

- **WHEN** the final stream event is assembled and the pipeline output has no answer value
- **THEN** the bare-answer field is left out of the event entirely rather than emitted as an
  empty string, so a producer-side extraction bug cannot masquerade as a legitimately empty
  answer and silently drop the model's answer from `/v1` content

#### Scenario: Final event without a bare answer never produces two lists

- **WHEN** the `final` event does not carry the bare-answer field (a defensive arm — the
  wrapper and endpoint ship in the same image, so this state indicates a producer regression)
- **THEN** the endpoint uses the event's `response` verbatim and does NOT append
  `format_citations`, so the content carries at most the wrapper's single appended source
  list and the duplicate-list defect cannot be reintroduced
