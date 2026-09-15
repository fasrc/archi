## ADDED Requirements

### Requirement: A malformed `last_message` is rejected with HTTP 400

The chat endpoints SHALL validate the shape of the request field `last_message` before any
of it is consumed, and SHALL reject a malformed value with **HTTP 400** and an error message
naming the expected shape. They SHALL NOT allow a malformed value to reach
`_prepare_chat_context`, where `sender, content = tuple(message[0])` (`app.py:1633`) unpacks
whatever the first element happens to be.

A value is well-formed when it is a non-empty list or tuple whose **first element** is itself
a list or tuple — never a string or bytes — of exactly two items, both of which are strings.
Every other value is malformed. Only the first element is validated, because only the first
element is read.

The 400 error message SHALL name the expected shape (a list containing a
`[sender, message]` pair, e.g. `[["User", "hello"]]`) rather than reporting a generic
"bad request", because the failure this rejects is one a caller cannot otherwise diagnose:
today it presents either as an opaque HTTP 500 or as a successful answer to the wrong
content.

#### Scenario: The canonical nested pair is accepted, unchanged

- **WHEN** a request sends `last_message = [["User", "How do I submit a job?"]]`
- **THEN** validation passes and yields `sender="User"`, `content="How do I submit a job?"`
- **AND** the request proceeds exactly as it does today — this change adds no new rejection
  for the shape both in-repo clients already send (`static/chat.js:266`,
  `openai_compat.py:242`)

#### Scenario: A tuple pair is accepted

- **WHEN** a request path supplies `last_message = [("user", "hello")]`, the shape
  `openai_compat.py:242` constructs
- **THEN** validation passes and yields `sender="user"`, `content="hello"`
- **AND** the accepted set is defined by structure, not by the concrete `list` type, so the
  OpenAI-compatible path is not broken by the new check

#### Scenario: A flat pair with a long sender returns 400, not 500

- **WHEN** a request sends the flat shape `last_message = ["User", "hello"]`
- **THEN** the endpoint returns **HTTP 400**
- **AND** it does NOT return HTTP 500, which is what `tuple("User")` yielding four items
  produces today

#### Scenario: A flat pair with a two-character sender returns 400, not a wrong answer

- **WHEN** a request sends the flat shape `last_message = ["AI", "hello"]`
- **THEN** the endpoint returns **HTTP 400**
- **AND** it does NOT return HTTP 200 with `sender="A"`, `content="I"`, which is what
  `tuple("AI")` produces today — the request currently succeeds while silently discarding
  the caller's message
- **AND** this is the regression scenario that motivates the change: it is the only failure
  mode here that is indistinguishable from success

#### Scenario: Empty, absent and null values return 400

- **WHEN** a request sends `last_message = []`, `last_message = null`, or omits the field
  entirely
- **THEN** the endpoint returns **HTTP 400** in each case
- **AND** the omitted-field case is included explicitly, because `_parse_chat_request`
  defaults it to `None` (`app.py:4606`) and a `None` reaching `message[0]` would otherwise
  raise inside the handler

#### Scenario: A first element that is not a two-item pair returns 400

- **WHEN** the first element is a one-item sequence, a three-item sequence, a mapping, a
  number, or `None` — for example `last_message = [["User"]]` or
  `last_message = [["User", "hello", "extra"]]`
- **THEN** the endpoint returns **HTTP 400**
- **AND** no truncation or padding is attempted; a pair that is not exactly two items is a
  malformed request, not a request to be repaired

#### Scenario: Non-string pair members return 400

- **WHEN** the first element is a two-item sequence whose sender or message is not a string
  — for example `last_message = [["User", 42]]` or `last_message = [[None, "hello"]]`
- **THEN** the endpoint returns **HTTP 400**
- **AND** the value is not coerced with `str()`, because a silently stringified payload is
  the same class of failure this requirement exists to remove

### Requirement: Both chat endpoints enforce the same validation

`POST /api/get_chat_response` and `POST /api/get_chat_response_stream` SHALL apply the same
`last_message` validation, because they accept the same payload through the same
`_parse_chat_request` (`app.py:4590`) and reach the same unpack.

The streaming endpoint SHALL return the rejection as an **ordinary HTTP 400 response**,
emitted before the NDJSON generator is constructed — not as an in-band
`{"type": "error", "status": 400}` event under HTTP 200. This matches how that endpoint
already reports its other pre-stream failure, the missing `client_id` check
(`app.py:4730`), and it keeps a malformed request visible to a client that checks only the
HTTP status.

#### Scenario: The non-streaming endpoint rejects with a JSON error body

- **WHEN** `POST /api/get_chat_response` receives a malformed `last_message`
- **THEN** the response status is `400` and the body is a JSON object carrying an `error`
  field that names the expected shape

#### Scenario: The streaming endpoint rejects before the stream opens

- **WHEN** `POST /api/get_chat_response_stream` receives a malformed `last_message`
- **THEN** the response status is `400` with a JSON error body
- **AND** no opening `meta` line is emitted and no NDJSON error event is produced, because
  the generator is never constructed
- **AND** a client that inspects only the HTTP status still sees the failure

#### Scenario: Validation runs before the chat pipeline is invoked

- **WHEN** either endpoint receives a malformed `last_message`
- **THEN** `ChatWrapper.chat` / `ChatWrapper.stream` is not called at all
- **AND** no conversation row is created for the rejected request, because
  `_prepare_chat_context` — which creates one when `conversation_id` is `None`
  (`app.py:1637`) — is never reached

### Requirement: The validation logic lives in a directly testable pure helper

The shape check SHALL live in its own module, `src/interfaces/chat_app/request_validation.py`,
as a pure function with no Flask, database or configuration dependency, and SHALL be unit
tested through that module directly rather than only through the endpoints.

This placement is a coverage and reuse requirement, not a style preference.
`src/interfaces/chat_app/app.py` is 4600+ lines and its request path is unreached by the
existing suite: four unit tests import the module, but none enters the body of
`_prepare_chat_context` or `get_chat_response`. New executable lines added inline there
land uncovered and fail the ≥80% diff-coverage gate. A pure helper is covered by cheap
direct tests, and is reused unchanged by both endpoints.

#### Scenario: The helper is exercised without an HTTP request

- **WHEN** a unit test imports the validation function from
  `src/interfaces/chat_app/request_validation.py`
- **THEN** it can assert accept and reject outcomes for every shape in this spec by calling
  the function directly
- **AND** it needs no Flask application, request context, database or configuration

#### Scenario: Both endpoints use the one helper

- **WHEN** the validation behaviour changes
- **THEN** it is changed in one place and both endpoints follow
- **AND** neither endpoint carries a second, inline copy of the shape check

### Requirement: The API reference documents the enforced contract

`docs/docs/api_reference.md` SHALL describe `last_message` validation as it behaves after
this change. It SHALL state that a malformed value is rejected with HTTP 400 on both
endpoints, and SHALL NOT continue to state that the endpoints do not validate the shape or
that a flat payload yields a 500 or a silently wrong answer.

The page currently documents the unvalidated behaviour in detail — a "The endpoint does not
currently validate the shape" sentence, and a paragraph splitting the flat-payload outcome
into a 500 case and a two-character-sender case that "succeeds against the wrong content".
Those passages become wrong on the day this lands, which is why the doc edit is part of this
change rather than a follow-up.

#### Scenario: The nested-shape section describes rejection, not silent failure

- **WHEN** a reader consults the "`last_message` is nested" section
- **THEN** it states that a value that is not a list containing a two-string pair is
  rejected with HTTP 400 on both endpoints
- **AND** it no longer claims the endpoint does not validate the shape, nor that a
  two-character sender succeeds with the wrong content

#### Scenario: The 400 is listed as a documented response

- **WHEN** a reader consults the response/error information for either chat endpoint
- **THEN** a malformed `last_message` is listed as a `400` with a JSON `error` body
- **AND** for the streaming endpoint the page states that this rejection arrives as a real
  HTTP status before the stream opens, alongside the existing pre-stream failures (`401`,
  the login `302`, and the missing-`client_id` `400`), rather than as an in-band event
