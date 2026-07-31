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
