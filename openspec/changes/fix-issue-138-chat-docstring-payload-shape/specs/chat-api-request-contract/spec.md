## ADDED Requirements

### Requirement: `last_message` is a list of `[sender, message]` pairs

The `get_chat_response` endpoint SHALL treat the request field `last_message` as a list
whose first element is a `[sender, message]` pair, and SHALL read only that first pair as
`(sender, content)`. The endpoint SHALL NOT accept a flat two-element list
(`["User", "hello"]`) as `last_message`; the canonical shape is `[["User", "hello"]]`. This
requirement documents the contract already enforced by `_prepare_chat_context`
(`app.py:1633`: `sender, content = tuple(message[0])`) and already produced by the in-repo
clients (`static/chat.js:266`, `openai_compat.py:242`).

#### Scenario: Nested pair is accepted

- **WHEN** a request sends `last_message = [["User", "How do I submit a job?"]]`
- **THEN** the handler unpacks the first element as `sender="User"`, `content="How do I submit a job?"`
- **AND** the request is processed without a payload-shape error

#### Scenario: Flat two-element list is not the accepted shape

- **WHEN** a request sends the flat shape `last_message = ["User", "hello"]`
- **THEN** `message[0]` is the string `"User"`, `tuple("User")` cannot unpack into
  `(sender, content)`, and the request fails with an unpacking error (HTTP 500 today)
- **AND** this flat shape is therefore not a valid `last_message` value

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
