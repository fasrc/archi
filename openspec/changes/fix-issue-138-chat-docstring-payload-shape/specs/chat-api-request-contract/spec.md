## ADDED Requirements

### Requirement: `last_message` is a list of `[sender, message]` pairs

The `get_chat_response` endpoint SHALL treat the request field `last_message` as a list
whose first element is a `[sender, message]` pair, and SHALL read only that first pair as
`(sender, content)`. The canonical shape is `[["User", "hello"]]`. This requirement
documents the shape `_prepare_chat_context` consumes (`app.py:1633`:
`sender, content = tuple(message[0])`) and the shape the in-repo clients already produce
(`static/chat.js:266`, `openai_compat.py:242`).

The endpoint performs **no shape validation**, so this requirement describes the canonical
shape rather than an enforced one. `tuple(message[0])` unpacks whatever the first element
happens to be, and a flat `last_message` therefore fails in one of two ways depending on
the sender's length — neither of them a rejection. Adding explicit validation is tracked
separately and is out of scope here.

#### Scenario: Nested pair is accepted

- **WHEN** a request sends `last_message = [["User", "How do I submit a job?"]]`
- **THEN** the handler unpacks the first element as `sender="User"`, `content="How do I submit a job?"`
- **AND** the request is processed without a payload-shape error

#### Scenario: Flat list with a sender of three or more characters fails loudly

- **WHEN** a request sends the flat shape `last_message = ["User", "hello"]`
- **THEN** `message[0]` is the string `"User"`, and `tuple("User")` yields four characters,
  which cannot unpack into `(sender, content)`
- **AND** the request fails with an unpacking error (HTTP 500 today), rather than a 400

#### Scenario: Flat list with a two-character sender fails silently

- **WHEN** a request sends the flat shape `last_message = ["AI", "hello"]`
- **THEN** `tuple("AI")` unpacks into `sender="A"`, `content="I"`
- **AND** the request **succeeds** while silently discarding the intended message, which is
  why the canonical shape must be documented rather than assumed to be enforced

### Requirement: The `get_chat_response` docstring documents the accepted `last_message` shape

The `get_chat_response` docstring SHALL describe `last_message` as a list containing a
single `[sender, message]` pair, with a concrete nested example (e.g.
`[["User", "How do I submit a job?"]]`), and SHALL state that only the first pair is read.
The docstring SHALL NOT describe `last_message` as a flat "list of length 2". The
docstring SHALL remain in agreement with the shape the handler consumes.

#### Scenario: Docstring shows the nested shape

- **WHEN** a reader inspects the `get_chat_response` docstring `last_message` description
- **THEN** it documents a list containing a `[sender, message]` pair with a concrete
  `[["User", ...]]` example
- **AND** it does not describe `last_message` as a flat "list of length 2"

#### Scenario: Docstring matches the handler

- **WHEN** the documented `last_message` example is sent as a request payload
- **THEN** it is the same shape `_prepare_chat_context` unpacks at `app.py:1633`, so a
  client built from the docstring does not hit the unpacking error
- **AND** note this covers the payload *shape* only; a request also needs the two timing
  fields to avoid HTTP 408, per the requirement below

### Requirement: The API reference documents the contract the endpoints actually honour

`docs/docs/api_reference.md` SHALL document the chat request body as the endpoints actually
treat it, not as the field names imply. Specifically it SHALL mark `client_sent_msg_ts` and
`client_timeout` as required in practice for as long as
[#175](https://github.com/fasrc/archi/issues/175) is open, SHALL publish an example that is a
request that succeeds, and SHALL mark `provider`, `model`, `include_agent_steps` and
`include_tool_steps` as honoured only by the streaming endpoint.

This requirement exists because both gaps are silent: omitting a timing field yields a 408
that names a timeout the caller never set, and sending an override field to the
non-streaming endpoint is discarded with no error at all. Neither is discoverable from the
field names.

#### Scenario: The published example completes a request

- **WHEN** an integrator copies the example request body from the API reference
- **THEN** it includes `client_sent_msg_ts` and `client_timeout`
- **AND** the request is not rejected with HTTP 408 by the check at `app.py:1654`
- **AND** the page states the example's authentication precondition, because every chat
  route is wrapped in `require_auth` (`app.py:2729`): with
  `services.chat_app.auth.enabled: true` the command receives `401` — or `302` when SSO is
  on and anonymous access is blocked — and never reaches the handler, so "a request that
  succeeds" is true only where auth is disabled or a session cookie is supplied
- **AND** the page shows how to obtain that cookie for a basic-auth deployment, and states
  that the SSO redirect flow cannot be completed with `curl`

#### Scenario: The example does not carry a hard-coded timestamp

- **WHEN** the API reference shows a runnable request
- **THEN** `client_sent_msg_ts` is generated at send time rather than written as a literal
  epoch value, because the check at `app.py:1654` compares it against the server clock and
  any literal is stale once it is older than `client_timeout`
- **AND** any non-runnable shape template marks the field with an unquoted placeholder, so
  pasting it unedited fails in the caller's JSON parser rather than reaching the server as a
  non-integer and surfacing as an opaque HTTP 500

#### Scenario: The streaming endpoint reports the rejection as an event, not a status

- **WHEN** the streaming endpoint rejects a request on the shared timeout check
- **THEN** the documentation states that the HTTP status is **200**, that an opening `meta`
  line is emitted first, and that the real status arrives in an
  `{"type": "error", "status": 408}` event (`app.py:2024`)
- **AND** it warns that a client checking only the HTTP status will read a failed request as
  a successful stream

#### Scenario: Pre-stream failures still report an ordinary HTTP status

- **WHEN** a streaming request fails *before* the response is constructed
  (`app.py:4768`) — an unauthenticated caller stopped by the `require_auth` wrapper, or a
  request omitting `client_id` (`app.py:4730`)
- **THEN** the documentation states that the failure arrives as a real HTTP status — `401`
  or a `302` redirect to login for authentication, `400` for the missing `client_id` — with
  no `meta` line and no `error` event, because the generator never runs
- **AND** the event-channel warning is scoped to failures raised *after* the stream opens,
  so the guidance is to check the HTTP status **and** inspect events — never to disregard
  status codes, which would make a client swallow a 401 as a successful stream

#### Scenario: `provider` and `model` are documented as jointly required

- **WHEN** a reader consults the override fields
- **THEN** the documentation states that the override is applied only under
  `if provider and model` (`app.py:2037`), so sending one alone is no override at all
- **AND** it states that this is silent — the default pipeline answers with no error or
  warning, so a caller can receive a reply from a model they did not request

#### Scenario: Timing fields are not presented as optional

- **WHEN** a reader consults the request-body table
- **THEN** `client_sent_msg_ts` and `client_timeout` are marked required in practice, with the
  408 behaviour and its cause stated
- **AND** the note records that this is a handler bug tracked as #175, not the intended
  contract, so the page can be simplified when that lands

#### Scenario: Override fields are marked stream-only

- **WHEN** a reader consults the request-body table
- **THEN** `provider`, `model`, `include_agent_steps` and `include_tool_steps` are marked as
  honoured only by `POST /api/get_chat_response_stream`
- **AND** the page states that sending them to `POST /api/get_chat_response` is silently
  ignored, because that handler never reads them off the parsed payload

### Requirement: The step flags are documented by what they gate, not by their names

`docs/docs/api_reference.md` SHALL describe `include_agent_steps` and `include_tool_steps` by
the events each one actually controls. `include_agent_steps` gates the incremental answer
text (`chunk`, `app.py:2365` and `:2399`); `include_tool_steps` gates the tool events **and**
the reasoning events (`thinking_start` / `thinking_end`, `app.py:2345` and `:2359`). The page
SHALL warn that the names invite the opposite reading.

#### Scenario: Reasoning events are documented as tool-flag controlled

- **WHEN** a reader consults either step flag
- **THEN** the page states that `thinking_start` and `thinking_end` are gated by
  `include_tool_steps`, not by `include_agent_steps`
- **AND** it states that `include_agent_steps: false` suppresses the streamed answer text
  while leaving reasoning events in place — failing twice for a caller who set it intending
  to hide reasoning
- **AND** it notes the resulting symptom is an answer arriving all at once in the `final`
  event rather than an error, so the mistake does not announce itself
- **AND** it states that reasoning cannot be suppressed independently of tool events through
  this API

### Requirement: The provider/model override is documented as an attempt, not a guarantee

The API reference SHALL describe sending `provider` and `model` together as an override that
is *attempted*, and SHALL organize the outcomes by **how the caller finds out** rather than as
a closed list of failure kinds. It SHALL cover: rejection as
`{"type": "error", "status": 400}` with the stream ending (`app.py:2048`); fallback announced
by a `{"type": "warning", ...}` event (`app.py:2052`, `:2073`); **silent** fallback, both when
`_create_provider_llm` returns falsey instead of raising — which is what an `ImportError` does
(`app.py:1611`) — and when the active pipeline exposes no `agent_llm` (`app.py:2055`); and
invocation-time failure surfacing as an in-band `{"type": "error", "status": 500}`
(`app.py:2568`). It SHALL NOT present `400` as the response to an unknown model ID.

#### Scenario: The fallback paths are documented, including the silent ones

- **WHEN** a reader consults the override behaviour
- **THEN** the page states that a **generic raised** construction exception, or a failed
  request-local pipeline build, yields a `warning` event and lets the default pipeline answer,
  so the request still succeeds
- **AND** it does NOT present construction failure as one uniform outcome: a construction-time
  `ValueError` emits an `error` event and **ends the stream** (`app.py:2042-2048`), while an
  `ImportError` returns `None` instead of raising (`app.py:1611-1613`) and falls back with no
  signal at all — the three are separate outcomes and this scenario is the acceptance check
  that keeps them separate
- **AND** it states that some failures produce **no** `error` and **no** `warning` at all —
  falsey construction such as an `ImportError`, and a pipeline without `agent_llm` — so a
  silent fallback is indistinguishable from success except by the reported model
- **AND** it directs the caller to read the reported model back from the response instead of
  inferring it from the request
- **AND** `warning` appears in the streaming event-type table, so a client enumerating event
  types does not treat it as unknown

#### Scenario: An unknown model ID is not documented as HTTP 400

- **WHEN** a reader consults what happens to a model ID the provider does not have
- **THEN** the page states that `get_chat_model` does not check the provider's catalogue, so
  for providers such as OpenAI and OpenRouter the string is accepted at construction time
- **AND** the failure is documented as arriving at invocation as an in-band
  `{"type": "error", "status": 500}` partway through the stream, not as a `400`
- **AND** the `400` claim is scoped to construction-time `ValueError` cases

### Requirement: `is_refresh` is documented as requiring a conversation ID

The API reference SHALL NOT present `is_refresh` as an independent optional switch. With no
`conversation_id`, the handler creates a *new* conversation with empty history
(`app.py:1639`) and then skips appending the caller's message because the request is a refresh
(`app.py:1657`), so there is no previous turn to re-answer and the pipeline is invoked with no
user turn at all.

#### Scenario: The refresh field states its dependency

- **WHEN** a reader consults `is_refresh` in the request-body table
- **THEN** it is marked as requiring `conversation_id`
- **AND** the consequence of omitting it is stated — a new empty conversation, the message
  dropped, and a pipeline invocation with no user turn

### Requirement: Event and failure lists are documented as open, not closed

The API reference SHALL present event types, gated-event categories and failure modes as the
cases a caller will meet rather than as exhaustive enumerations, and SHALL instruct clients to
tolerate unseen event types and statuses. The chat handler's emitted events vary by pipeline
and its failure modes vary by provider, so a closed list is a claim the code does not support.
Recording this as a requirement makes a future edit that tightens a list into an exhaustive
claim a visible regression rather than an apparent improvement.

#### Scenario: The page states that its lists are not exhaustive

- **WHEN** a reader begins the page
- **THEN** it states that lists of events, error statuses and failure modes are categories and
  examples rather than closed enumerations
- **AND** it instructs clients to ignore unknown event `type` values rather than fail on them

#### Scenario: Legacy `step` events are covered by the tool-flag contract

- **WHEN** a reader consults what `include_tool_steps` gates
- **THEN** the legacy `step` events emitted by non-agent pipelines (`app.py:2386` →
  `app.py:1701`) are named alongside the tool and reasoning events
- **AND** `step` appears in the streaming event-type table with its `step_type` values, so a
  client built from the table does not discard tool updates from a supported pipeline

#### Scenario: The flat-payload failure is split by endpoint

- **WHEN** a reader consults what happens when `last_message` is sent in the flat form
- **THEN** the unpacking failure is documented as HTTP 500 for `POST /api/get_chat_response`
  and as an in-band `{"type": "error", "status": 500}` under HTTP 200 for the streaming
  endpoint, since the exception is raised inside the generator (`app.py:2568`)
- **AND** the two-character-sender case is documented as succeeding silently on **both**
  endpoints
