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

### Requirement: An unrepresentable client send time is refused, not crashed

Both chat endpoints SHALL reject a request whose supplied `client_sent_msg_ts` cannot be
converted by `datetime.fromtimestamp` with **HTTP 400**, and SHALL do so before invoking the
pipeline and before any conversation or timestamp row is written. A falsey value SHALL NOT be
treated as unrepresentable — that is the documented optional case.

The unconditional deadline check used to screen these values out incidentally: any absurd
`client_sent_msg_ts` made `server_received_msg_ts - client_sent_msg_ts` exceed the timeout, so
the request was refused with 408 before the pipeline ran. Requiring a truthy `client_timeout`
removes that accident, and the value then reaches `datetime.fromtimestamp` at persistence time
instead — raising `OSError` beyond the platform's `time_t`, `OverflowError`, or `ValueError`
outside years 1–9999. On the non-streaming route that is a 500 after generation has been paid
for; on the streaming route the caller has already received HTTP 200 and the failure lands
mid-stream.

The check SHALL be the conversion itself rather than a hardcoded range, so that it cannot
disagree with the two call sites it protects about where the boundary lies.

**Normalization is part of the validated step, not a preliminary to it.** Both timing fields
arrive in milliseconds and are divided by 1000, and that division is itself failable on
well-formed JSON: a 1001-digit integer raises `OverflowError`, and a quoted number raises
`TypeError`. A range check placed after the division therefore never runs for those inputs, and
the endpoint returns 500. Normalization and validation SHALL happen in one guarded step, and
SHALL apply to **both** `client_sent_msg_ts` and `client_timeout` — `client_timeout` needs no
range check, since it is only compared against an elapsed interval, but it is divided the same
way and so is exposed the same way.

#### Scenario: A value whose normalization overflows is refused, not crashed

- **WHEN** a request supplies a `client_sent_msg_ts` or `client_timeout` that cannot be divided
  — an integer too large to become a float, or a non-numeric value
- **THEN** the endpoint returns HTTP 400 with an error naming that field
- **AND** the pipeline is not invoked

#### Scenario: An unrepresentable timestamp is refused before any work

- **WHEN** a request supplies a `client_sent_msg_ts` that `datetime.fromtimestamp` cannot
  convert, with or without a `client_timeout`
- **THEN** the endpoint returns HTTP 400 with an error naming `client_sent_msg_ts`
- **AND** the pipeline is not invoked

#### Scenario: A representable timestamp is still accepted

- **WHEN** a request supplies a normal millisecond timestamp
- **THEN** it is converted to seconds and processed as before

#### Scenario: An absent timestamp is not an invalid one

- **WHEN** a request omits `client_sent_msg_ts`
- **THEN** the request is processed normally and no 400 is returned

### Requirement: A timing row records an absent client send time as a specified sentinel

A request that omits `client_sent_msg_ts` and completes SHALL still have its `timing` row
written, and the absent send time SHALL be recorded as the Unix epoch,
`1970-01-01T00:00:00Z`, which SHALL be documented as meaning "the client declared no send
time" so a caller computing client→server latency can exclude it.

Accepting such a request makes it reach `insert_timing` for the first time — on `dev` it was
refused with 408 before ever getting there. The column `timing.client_sent_msg_ts` is
`TIMESTAMPTZ NOT NULL` (`src/cli/templates/init.sql:476`), so the row must carry a value and
"unknown" has no representation. The row is still written because its other ten milestones are
real measurements and are what the shipped Grafana panels plot
(`src/cli/templates/grafana/archi-default-dashboard.json` keys off `server_received_msg_ts` and
`msg_duration`, not this column).

A server-side substitute — recording `server_received_msg_ts` in its place — SHALL NOT be used:
it reads as a genuinely instantaneous client hop and no query can distinguish it from a real
measurement, which is the silent-corruption failure mode this project has been removing
elsewhere (issue #178).

Making the column nullable is the correct end state and is explicitly out of scope here: the
code change alone raises `NotNullViolation` on any deployment whose schema predates the
migration, and issue #180 records that migrations are not applied to existing deployments. The
sequencing is therefore #180 first, then the nullable column, then this sentinel is retired.

#### Scenario: A request with no send time still gets a timing row

- **WHEN** a request omits `client_sent_msg_ts` and completes
- **THEN** `insert_timing` is called for that message
- **AND** the server-side milestones on the row are real timestamps

#### Scenario: The absent value is the epoch, not a substitute

- **WHEN** the persisted `client_sent_msg_ts` is read for such a request
- **THEN** it is `1970-01-01T00:00:00Z`
- **AND** it is not equal to `server_received_msg_ts`

#### Scenario: A supplied send time is unaffected

- **WHEN** a request supplies `client_sent_msg_ts`
- **THEN** the persisted value is that timestamp, not the sentinel

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
