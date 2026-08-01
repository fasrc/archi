## ADDED Requirements

### Requirement: A refresh with no surviving prior turn is rejected

The chat endpoints SHALL reject a request with `is_refresh` true for which **no prior turn survives
history resolution and the refresh trim**, and SHALL do so before any conversation row is created or
any conversation timestamp is updated.

The rejection SHALL be reported on each endpoint's own error channel, which differ:
`POST /api/get_chat_response` SHALL return HTTP status `400`, while
`POST /api/get_chat_response_stream` SHALL return HTTP **200** and report the rejection as an in-band
`{"type": "error", "status": 400}` event. The streaming response is constructed before
`_prepare_chat_context` runs, so its status line is already sent by the time this check happens —
specifying a bare "HTTP 400" for both would make the shipped streaming behaviour non-compliant and
invite a later implementation to "fix" it by breaking the documented streaming contract.

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
- **THEN** the handler returns error status `400` and no chat context, surfaced on each endpoint's
  own channel per the requirement above
- **AND** the caller's message is not silently discarded in favour of an empty prompt

#### Scenario: Refresh whose resolved history holds no prior turn is rejected

- **WHEN** a refresh supplies `external_history` of `[]`, or a history containing only assistant
  turns, or names a `conversation_id` whose conversation holds no turns
- **THEN** each is rejected with error status `400`, surfaced on each endpoint's own channel
- **AND** the rejection is decided after the refresh trim, so all three routes to the same empty
  state are covered by one check rather than by three special cases

#### Scenario: A refresh rejected for missing history writes nothing

- **WHEN** a refresh is rejected because no prior turn survives resolution and the trim
- **THEN** `create_conversation` is not called
- **AND** `update_conversation_timestamp` is not called
- **AND** no empty conversation row is left behind, because history resolution is side-effect free
  and the writes are committed only once this check has passed
- **AND** this guarantee is scoped to *this* rejection: later rejections on the same path — the
  timeout `408` and the query-limit `500` — are decided after the writes and are unchanged by this
  change, so it would be false to promise that no rejected request ever writes

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
mapping, so that a status added to one endpoint cannot be missing from the other. This SHALL include
the streaming endpoint's **exception branches**, not only its context-error branch.

Before this change the mapping was duplicated at the streaming call site (`app.py:2019-2025`) and in
the non-streaming route (`:4668-4674`), each knowing only `408` and `403` and falling through to
"server error; see chat logs for message" — which would report a client error as a server fault.

Three further copies lived in the streaming generator's own error paths: the
`ConversationAccessError` branch, the generic-exception branch, and the no-output branch each
hard-coded their `403`/`500` text. The strings agreed with the mapping by coincidence, so the
duplication was invisible — and editing the shared text would silently have made the endpoints
disagree, which is precisely the failure this requirement exists to prevent. A requirement that
holds only where someone remembered to apply it is not a requirement.

Out of scope: the trace-metadata route's `404 "conversation not found"`, which is a different status
on a different endpoint and is not part of the chat error mapping.

#### Scenario: The refresh rejection is not described as a server error

- **WHEN** either endpoint rejects a refresh because no prior turn survives
- **THEN** the message identifies the request as unsatisfiable and names the missing precondition
- **AND** it is not the generic "server error; see chat logs for message" text
- **AND** the requirement is specific to *this* rejection: the other `400`s on these routes carry
  their own text — a missing `client_id` before the handler is entered, and a streaming
  provider-override `ValueError` — and must not claim that prior history is missing

#### Scenario: Existing status messages are unchanged

- **WHEN** either endpoint returns `408` or `403`
- **THEN** the message is the same text as before this change
- **AND** an unrecognized status still falls back to the generic server-error text

#### Scenario: Both endpoints agree

- **WHEN** the same error status is produced by the streaming and non-streaming endpoints
- **THEN** both report the same message, because both read it from the shared mapping
- **AND** this holds for the streaming endpoint's exception branches too, so editing a mapped string
  cannot make one path disagree with the others
