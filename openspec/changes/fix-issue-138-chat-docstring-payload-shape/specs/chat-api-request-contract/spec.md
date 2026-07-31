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
