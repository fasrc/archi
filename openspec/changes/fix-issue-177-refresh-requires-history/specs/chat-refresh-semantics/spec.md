## ADDED Requirements

### Requirement: A refresh with nothing to refresh is rejected

The chat endpoints SHALL reject a request with `is_refresh` true that supplies **no source of prior
turns** — neither a `conversation_id` nor an `external_history` — with HTTP status `400`, and SHALL
do so before any conversation row is created.

The condition is the absence of prior turns, not the absence of a `conversation_id`. Without this
guard the handler resolves an empty history (`app.py:1639-1641`), performs a no-op refresh trim
(`:1650-1652`), and then skips appending the caller's message because the request is a refresh
(`:1657-1658`) — invoking the pipeline with no user turn at all and returning a confident answer to
an empty prompt. That outcome is indistinguishable from success, which is why it must be an explicit
rejection rather than a best-effort interpretation.

#### Scenario: Refresh with neither a conversation nor supplied history is rejected

- **WHEN** a request sets `is_refresh` true with `conversation_id` absent and no `external_history`
- **THEN** the handler returns error status `400` and no chat context
- **AND** the caller's message is not silently discarded in favour of an empty prompt

#### Scenario: A rejected refresh creates no conversation

- **WHEN** that same request is rejected
- **THEN** `create_conversation` is not called
- **AND** no empty conversation row is left behind, because the guard runs before the branch that
  would create one

#### Scenario: Refresh over supplied history is still honoured

- **WHEN** a request sets `is_refresh` true with no `conversation_id` but supplies
  `external_history` containing prior turns
- **THEN** the request is **not** rejected
- **AND** the supplied history is trimmed of its trailing assistant turns and re-answered, because
  supplied turns are a valid source of prior turns

#### Scenario: Refresh against an existing conversation is unchanged

- **WHEN** a request sets `is_refresh` true with a `conversation_id`
- **THEN** the stored history is loaded, trailing assistant turns are trimmed, and the incoming
  message is **not** appended
- **AND** the behaviour is exactly as before this change, so the fix cannot be "always append"

#### Scenario: A first message that is not a refresh is unchanged

- **WHEN** a request sets `is_refresh` false with no `conversation_id`
- **THEN** a conversation is created and the caller's message is appended to the empty history
- **AND** the ordinary first-turn path is unaffected by the guard

### Requirement: Chat error statuses carry a caller-appropriate message on both endpoints

Both chat endpoints SHALL derive the human-readable text for an error status from a single shared
mapping, so that a status added to one endpoint cannot be missing from the other.

Before this change the mapping was duplicated at the streaming call site (`app.py:2019-2025`) and in
the non-streaming route (`:4668-4674`), each knowing only `408` and `403` and falling through to
"server error; see chat logs for message" — which would report a client error as a server fault.

#### Scenario: A client-error status is not described as a server error

- **WHEN** either endpoint rejects a request with status `400`
- **THEN** the message identifies the request as unsatisfiable and names the missing precondition
- **AND** it is not the generic "server error; see chat logs for message" text

#### Scenario: Existing status messages are unchanged

- **WHEN** either endpoint returns `408` or `403`
- **THEN** the message is the same text as before this change
- **AND** an unrecognized status still falls back to the generic server-error text

#### Scenario: Both endpoints agree

- **WHEN** the same error status is produced by the streaming and non-streaming endpoints
- **THEN** both report the same message, because both read it from the shared mapping
