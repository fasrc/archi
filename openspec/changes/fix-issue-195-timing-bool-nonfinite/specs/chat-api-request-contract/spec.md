## ADDED Requirements

### Requirement: A JSON boolean in a client timing field is refused

Both chat endpoints SHALL reject a request supplying a JSON boolean for `client_sent_msg_ts` or `client_timeout` with **HTTP 400**, naming the offending field, and SHALL do so before the pipeline is invoked and before any conversation or timing row is written.

`bool` is a subclass of `int` in Python, so a boolean divides by 1000 without raising and is
neither an `OverflowError` nor a `TypeError`. It therefore passes the guard that refuses every
other non-numeric type. `true` becomes `0.001` — a 1-millisecond deadline, so a request that
also supplies `client_sent_msg_ts` is past its deadline on arrival and comes back 408 with
nothing indicating the *field* was at fault; as a `client_sent_msg_ts` it persists as
`1970-01-01T00:00:00.001Z`, which is not the specified absent-value sentinel and is
indistinguishable from a real measurement at a glance.

The check SHALL be placed before the falsey guard that maps an absent field to `0`. `False` is
falsey, so a check placed after that guard cannot see it, and `false` would go on being
accepted as "field omitted" while `true` was refused — the two booleans would diverge for no
stated reason.

Rejecting a boolean SHALL NOT be implemented by narrowing the falsey guard itself. That guard
is specified: any falsey value means "not supplied". Narrowing it would change the contract
for `""`, `[]` and `{}` as collateral damage.

#### Scenario: `true` in either timing field is refused

- **WHEN** a request supplies `true` for `client_sent_msg_ts` or for `client_timeout`
- **THEN** the endpoint returns HTTP 400 with an error naming that field
- **AND** the pipeline is not invoked
- **AND** this holds on both the non-streaming and the streaming route

#### Scenario: `false` in either timing field is refused, not read as omission

- **WHEN** a request supplies `false` for `client_sent_msg_ts` or for `client_timeout`
- **THEN** the endpoint returns HTTP 400 with an error naming that field
- **AND** the request is **not** processed as though the field had been omitted

#### Scenario: An omitted field is still not an invalid one

- **WHEN** a request omits `client_sent_msg_ts`, `client_timeout`, or both
- **THEN** no 400 is returned and the request is processed normally
- **AND** the absent field is observable downstream as `0`, exactly as before

### Requirement: A non-finite client timing value is refused

Both chat endpoints SHALL reject a request supplying a non-finite number — `Infinity`, `-Infinity` or `NaN` — for `client_sent_msg_ts` or `client_timeout` with **HTTP 400**, naming the offending field, before the pipeline is invoked.

Python's `json` module accepts the bare `NaN`, `Infinity` and `-Infinity` tokens by default,
and that is the decoder the chat routes receive their body from, so a caller can reach these
values with a body the server considers well-formed. `inf / 1000` is `inf` and `nan / 1000` is
`nan`, so neither raises and both are used as a deadline verbatim. Every comparison against
`NaN` evaluates `False`, so a `NaN` timeout disables the deadline outright while appearing to
declare one — a silent failure the caller has no way to observe.

The check SHALL be applied after the millisecond→second division rather than before it, so
that it is only ever handed a real number: a non-numeric value has already raised `TypeError`
and an over-large integer has already raised `OverflowError` by that point. The predicate
SHALL reject the infinities as well as `NaN`, because both are broken in the same way.

#### Scenario: A non-finite timeout is refused instead of disabling the deadline

- **WHEN** a request supplies `Infinity`, `-Infinity` or `NaN` for `client_timeout`
- **THEN** the endpoint returns HTTP 400 with an error naming `client_timeout`
- **AND** the value is not carried through as a deadline of any kind

#### Scenario: A non-finite send time is refused as a type problem

- **WHEN** a request supplies `Infinity`, `-Infinity` or `NaN` for `client_sent_msg_ts`
- **THEN** the endpoint returns HTTP 400 with an error naming `client_sent_msg_ts`
- **AND** the refusal happens during normalization, not at the representable-time check that
  refuses it today

#### Scenario: A large finite timeout is still accepted

- **WHEN** a request supplies a large but finite `client_timeout`
- **THEN** it is accepted and means "no deadline in practice", as already specified
- **AND** only non-finite values are refused by this requirement

### Requirement: The values that were already accepted are still accepted

This change SHALL narrow only the set of truthy values the timing fields accept, and SHALL NOT alter the handling of any value that is accepted today other than a boolean or a non-finite number.

This requirement exists because the most likely way to implement the boolean rejection wrongly
is to disturb the falsey-means-absent rule, which several other specified behaviours depend
on. Negative and fractional values are explicitly outside this change: a negative
`client_sent_msg_ts` inside years 1–9999 is an ordinary pre-1970 timestamp.

#### Scenario: A real millisecond value round-trips unchanged

- **WHEN** a request supplies `client_timeout` of `600000` and `client_sent_msg_ts` of
  `1700000000000`
- **THEN** they are normalized to `600.0` and `1700000000.0` respectively
- **AND** the request is processed as before

#### Scenario: The existing refusals are unchanged

- **WHEN** a request supplies a quoted number, a non-empty array, a non-empty object, or an
  integer too large to become a float, in either timing field
- **THEN** the endpoint still returns HTTP 400 naming that field

#### Scenario: A negative or fractional value is still accepted

- **WHEN** a request supplies a negative `client_sent_msg_ts` naming a representable pre-1970
  date, or a fractional millisecond value
- **THEN** it is accepted, and this change introduces no new refusal for it

### Requirement: The API reference lists a boolean and a non-finite literal among the rejected values

`docs/docs/api_reference.md` SHALL list a JSON boolean and a non-finite literal among the values rejected with 400 in both the `client_sent_msg_ts` and the `client_timeout` rows of the chat request-body table, and SHALL NOT continue to describe either as an accepted-but-badly-behaved input.

The page currently documents the hole accurately and points at the issue: that a boolean
"slips through this rule as a usable number instead of being refused", that `true` becomes a
1 ms deadline, and that "a literal that decodes to infinity or `NaN` disables the deadline
outright". Once they are refused, those passages SHALL be replaced by the rejection, and the
`issue #195` pointers SHALL be removed rather than left pointing at a closed issue.

No `[name]: …/app.py#Lnnn` anchor definition on the page SHALL be renumbered by this change —
that is issue #190 — and no `app.py` line is affected by it, so every anchor stays valid.

#### Scenario: Both rows name the newly rejected values

- **WHEN** a reader consults either timing-field row
- **THEN** a boolean and a non-finite literal appear in that row's list of values rejected
  with 400
- **AND** the row no longer states that a boolean is usable as a number, that `true` yields a
  1 ms deadline, or that infinity or `NaN` disables the deadline

#### Scenario: The issue pointers are retired

- **WHEN** the page is read after this change
- **THEN** no reference to issue #195 as an open defect remains in either row

#### Scenario: The anchors are untouched

- **WHEN** the diff of the page against `origin/dev` is inspected
- **THEN** no line containing an `#L<number>` anchor target has changed
