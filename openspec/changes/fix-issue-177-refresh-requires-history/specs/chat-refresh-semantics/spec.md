## ADDED Requirements

### Requirement: A refresh with no surviving prior turn is rejected

The chat endpoints SHALL reject a request with `is_refresh` true for which **no prior turn survives
history resolution and the refresh trim**, with HTTP status `400`, and SHALL do so before any
conversation row is created or any conversation timestamp is updated.

The test is on the **resolved** history, not on which request fields were supplied. Testing for a
supplied source instead is a proxy for "prior turns exist", and the proxy admits three requests that
reach the same unsatisfiable state: an `external_history` of `[]` (which is not `None`), a history
of assistant turns only (which the trim empties), and a `conversation_id` naming a conversation that
holds no turns.

In any of those states the handler would perform a no-op trim and then skip appending the caller's
message because the request is a refresh — invoking the pipeline with no user turn at all and
returning a confident answer to an empty prompt. That outcome is indistinguishable from success,
which is why it must be an explicit rejection rather than a best-effort interpretation.

#### Scenario: Refresh with neither a conversation nor supplied history is rejected

- **WHEN** a request sets `is_refresh` true with `conversation_id` absent and no `external_history`
- **THEN** the handler returns error status `400` and no chat context
- **AND** the caller's message is not silently discarded in favour of an empty prompt

#### Scenario: Refresh whose resolved history holds no prior turn is rejected

- **WHEN** a refresh supplies `external_history` of `[]`, or a history containing only assistant
  turns, or names a `conversation_id` whose conversation holds no turns
- **THEN** each is rejected with status `400`
- **AND** the rejection is decided after the refresh trim, so all three routes to the same empty
  state are covered by one check rather than by three special cases

#### Scenario: A rejected refresh writes nothing

- **WHEN** any refresh is rejected
- **THEN** `create_conversation` is not called
- **AND** `update_conversation_timestamp` is not called
- **AND** no empty conversation row is left behind, because history resolution is side-effect free
  and the writes are committed only once the request is known to be serviceable

#### Scenario: Refresh over supplied history is still honoured

- **WHEN** a request sets `is_refresh` true with no `conversation_id` but supplies
  `external_history` containing a prior user turn
- **THEN** the request is **not** rejected
- **AND** the supplied history is trimmed of its trailing assistant turns and re-answered, because
  supplied turns are a valid source of prior turns

#### Scenario: Supplied history is not mutated by the refresh trim

- **WHEN** a refresh is served against a supplied `external_history`
- **THEN** the caller's list is unchanged after the call
- **AND** the trim operates on a copy, because popping from the caller's argument is a side effect
  the handler has no business having

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
