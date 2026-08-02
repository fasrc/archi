## ADDED Requirements

### Requirement: The client timing fields are optional

`POST /api/get_chat_response` and `POST /api/get_chat_response_stream` SHALL accept a request
body that omits `client_sent_msg_ts`, `client_timeout`, or both, and SHALL process such a
request normally rather than rejecting it with HTTP 408. An absent client deadline SHALL mean
"the client has declared no deadline", which is how the streaming loop already reads the same
value (`app.py:2156`, `if client_timeout and ...`).

`_parse_chat_request` coerces an absent field to `0` (`app.py:4654-4655`), so within
`_prepare_chat_context` "absent" is observable as `0`. The requirement is therefore that a
`client_timeout` of `0`, or a `client_sent_msg_ts` of `0`, disables the deadline check. A
`client_timeout` supplied without a `client_sent_msg_ts` has no baseline to measure elapsed
time from, so it too SHALL disable the check rather than measure from the epoch.

#### Scenario: Both timing fields omitted

- **WHEN** a request omits both `client_sent_msg_ts` and `client_timeout`, so
  `_prepare_chat_context` receives `client_sent_msg_ts=0` and `client_timeout=0`
- **THEN** the deadline check does not fire
- **AND** the call returns a populated `ChatRequestContext` with no error status, rather than
  `(None, 408)`

#### Scenario: Only `client_timeout` supplied

- **WHEN** a request supplies `client_timeout` but omits `client_sent_msg_ts`, so
  `_prepare_chat_context` receives `client_sent_msg_ts=0` and a non-zero `client_timeout`
- **THEN** the deadline check does not fire, because elapsed time would otherwise be measured
  from the Unix epoch and exceed any finite timeout
- **AND** the request is processed normally

#### Scenario: Only `client_sent_msg_ts` supplied

- **WHEN** a request supplies `client_sent_msg_ts` but omits `client_timeout`, so
  `_prepare_chat_context` receives a non-zero `client_sent_msg_ts` and `client_timeout=0`
- **THEN** the deadline check does not fire, because a timeout of `0` means no declared
  deadline rather than a deadline of zero seconds
- **AND** the request is processed normally

### Requirement: An explicitly-supplied client deadline is still enforced

`_prepare_chat_context` SHALL continue to return HTTP 408 when a request supplies both
`client_sent_msg_ts` and a non-zero `client_timeout` and the interval between the client's
send time and `server_received_msg_ts` exceeds that timeout. Making the timing fields optional
SHALL NOT be implemented by removing the check.

#### Scenario: A genuinely exceeded deadline is rejected

- **WHEN** a request supplies a `client_sent_msg_ts` and a non-zero `client_timeout`, and
  `server_received_msg_ts` is later than `client_sent_msg_ts + client_timeout`
- **THEN** `_prepare_chat_context` returns `(None, 408)`

#### Scenario: A deadline not yet reached is accepted

- **WHEN** a request supplies a `client_sent_msg_ts` and a non-zero `client_timeout`, and
  `server_received_msg_ts` falls within that window
- **THEN** the deadline check does not fire and the request is processed normally

### Requirement: The two timeout checks state their relationship in the code

Each timeout check SHALL carry a comment naming the other — the deadline check in
`_prepare_chat_context` and the in-stream check in the streaming loop — recording that both
read a falsey `client_timeout` as "no deadline" and that they intentionally measure from
different baselines: the client's send time in the first, `stream_start_time` in the second.

This requirement exists because the defect was precisely a silent divergence between the two:
one site guarded the comparison and the other did not, and nothing in either site pointed at
its twin.

#### Scenario: Each site references its twin

- **WHEN** a maintainer reads either timeout check
- **THEN** a comment at that site names the other check and states that both treat a falsey
  `client_timeout` as "no client deadline"
- **AND** it records that the differing baselines are deliberate, not a bug to be "fixed" by
  making them identical

### Requirement: The API reference documents the timing fields as optional

`docs/docs/api_reference.md` SHALL describe `client_sent_msg_ts` and `client_timeout` as
optional, SHALL NOT carry the "required in practice" warning or the 408 reproduction table
added by PR #159, and SHALL state what the fields are actually for: latency accounting, and
declaring a client deadline the server will honour when it is supplied.

The published example SHALL remain a request that succeeds. Where the example retains
`client_sent_msg_ts`, it SHALL still be generated at send time rather than written as a
literal epoch value, because a stale literal combined with a supplied `client_timeout` is a
genuinely expired deadline and is still rejected with 408.

#### Scenario: The request-body table marks both fields optional

- **WHEN** a reader consults the chat request-body table
- **THEN** `client_sent_msg_ts` and `client_timeout` are marked optional
- **AND** the table does not state or imply that omitting either yields HTTP 408

#### Scenario: The obsolete warning is removed

- **WHEN** a reader reads the page after this change
- **THEN** the admonition describing the fields as required in practice, its worked 408 table,
  and its reference to issue #175 as an open bug are gone
- **AND** nothing remains on the page that instructs an integrator to send the fields in order
  to avoid rejection

#### Scenario: The example still completes a request

- **WHEN** an integrator copies the example request body from the API reference
- **THEN** the request is accepted, whether or not the example carries the timing fields
- **AND** any retained `client_sent_msg_ts` is generated at send time, so the example does not
  become a request that 408s once it is stale
