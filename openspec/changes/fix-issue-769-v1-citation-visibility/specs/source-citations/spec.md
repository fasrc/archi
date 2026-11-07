## ADDED Requirements

### Requirement: Source visibility applies to /v1 citations
The `/v1/chat/completions` endpoint SHALL leave out of its citation block every source document that `is_visible` hides under the chat wrapper's `sources_config`, in streaming and in non-streaming mode, so that `data_manager.sources.<name>.visible: false` hides a source in `/v1` citations as it does in the native chat's source list.

The endpoint SHALL drop each hidden document together with its own score, so that every
kept document keeps the score at its own position in the event's `retriever_scores`, and a
kept document with no score keeps none. A chat wrapper whose `sources_config` is absent or is
not a dict SHALL filter nothing. `format_citations` SHALL stay a pure formatter with no
visibility check. The requirement covers the appended citation block only: a hidden document
still reaches the model, and the model's own answer text is passed through as written.

#### Scenario: A hidden source is left out with its score, non-streaming

- **WHEN** `sources_config` has `jira.visible: false` and `git.visible: true`
- **AND** a non-streaming `/v1` request's `final` event carries a `ticket` document with
  `ticket_provider` `jira` and then a `git` document, with `retriever_scores` `[0.9, 0.4]`
- **THEN** the message content has one `**Sources:**` block that does not name the Jira
  document
- **AND** the block lists the `git` document with `(relevance: 0.40)`

#### Scenario: A hidden source is left out with its score, streaming

- **WHEN** the same configuration and `final` event arrive on a streaming `/v1` request
- **THEN** the content joined from the SSE chunks has one `**Sources:**` block that does not
  name the Jira document and lists the `git` document with `(relevance: 0.40)`

#### Scenario: Only hidden sources give no citation block

- **WHEN** every document in the `final` event comes from a source with `visible: false`
- **THEN** the `/v1` content, streaming or non-streaming, is the answer with no
  `**Sources:**` block

#### Scenario: A short score list stays aligned

- **WHEN** the documents are a hidden document, then two visible documents, and the scores
  are `[0.9, 0.4]`
- **THEN** the filter keeps the two visible documents and the scores `[0.4]`, so the second
  visible document has no score and is listed without a relevance value

#### Scenario: No sources config means no filter

- **WHEN** the chat wrapper has no `sources_config` attribute, or its value is not a dict
- **THEN** every document is cited with its own score, and the filter writes no log record
