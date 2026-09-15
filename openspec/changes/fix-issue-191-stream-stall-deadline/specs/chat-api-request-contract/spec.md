## ADDED Requirements

### Requirement: A declared client deadline bounds a stalled provider

`POST /api/get_chat_response_stream` SHALL enforce a truthy `client_timeout` around *advancement* of the upstream generator, so that a provider which accepts the request and then produces no event is ended at the deadline rather than awaited indefinitely.

The enforcement SHALL be observationally identical to the existing in-loop check: the same
in-band `{"type": "error", "status": 408}` event, and the same trace closure with
`status="error"`, `cancelled_by="system"`, `cancellation_reason="Client timeout"` and a
populated `total_duration_ms`. A client cannot tell which of the two guards fired, and
neither can a trace reader.

This closes a gap, not a regression: the check at `app.py:2174` is the first statement of
the consume loop's body and is therefore reached only when the generator yields. Nothing
below the endpoint supplies a fallback — no provider or pipeline in `src/archi/` sets a
request timeout.

A falsey `client_timeout` SHALL continue to mean "no deadline declared", and SHALL NOT
cause the request to be advanced through a worker at all. The no-deadline path keeps
iterating the generator directly on the request thread.

#### Scenario: A provider that never yields is ended at the deadline

- **WHEN** a streaming request supplies a truthy `client_timeout` and the upstream generator
  blocks past that deadline without yielding
- **THEN** the response carries an in-band `{"type": "error", "status": 408}` event
- **AND** the trace is closed with `status="error"`, `cancelled_by="system"` and
  `cancellation_reason="Client timeout"`
- **AND** the stream ends rather than waiting for the provider to return

#### Scenario: A provider that answers within budget is unaffected

- **WHEN** a streaming request supplies a `client_timeout` and the generator yields its
  events within that budget
- **THEN** the stream completes normally through its `final` event
- **AND** no 408 event is produced

#### Scenario: No declared deadline means no worker

- **WHEN** a streaming request supplies a falsey `client_timeout`
- **THEN** the generator is advanced directly on the request thread, with no executor and
  no worker created
- **AND** the stream behaves exactly as it did before this change

### Requirement: The stall deadline is measured on a monotonic clock

The stall deadline SHALL be computed from `time.monotonic`, so that an adjustment to the system wall clock during a stream can neither extend nor collapse a deadline the client declared.

The monotonic function SHALL be bound at module import rather than reached as an attribute
of the `time` module at call time. This is a behavioural requirement, not a style
preference: the existing test that pins the two guards' asymmetry replaces the module's
`time` global with a stub exposing only `time`
(`tests/unit/test_chat_timeout_guard.py:209-211`), and an attribute lookup would raise
`AttributeError` there. That test SHALL pass unmodified.

#### Scenario: The existing in-stream guard test is untouched

- **WHEN** the suite runs `TestTheInStreamCheckNeedsOnlyTheTimeout` with its stubbed `time`
  module
- **THEN** both of its tests pass without any edit to the test file

#### Scenario: The wall clock does not move the deadline

- **WHEN** the system wall clock jumps while a stream is waiting on an advance
- **THEN** the remaining budget for that advance is unchanged, because it is derived from
  the monotonic clock

### Requirement: Advancing on a worker preserves the caller's execution context

Each advance performed on a worker SHALL execute inside a snapshot of the request thread's `contextvars` context, and every advance of a given stream SHALL reuse that same snapshot.

Without the snapshot, code the pipeline already runs would change behaviour silently and in
the permissive direction: the RBAC tool gate at
`src/archi/pipelines/agents/tools/base.py:36-42` treats "no request context" as *allow*, so
every tool permission check in a streaming request would pass unchecked, and
`get_role_context()` (`src/archi/pipelines/agents/utils/prompt_utils.py:14-18`) would return
an empty string and drop the user's roles from the prompt. Neither failure raises.

Reusing one snapshot is what carries context mutations forward between advances.
`start_run_memory()` runs on the *first* advance (`base_react.py:1418` via
`_prepare_agent_inputs`), so a per-advance snapshot would leave `_ACTIVE_MEMORY` unset from
the second advance onward, and `if self.active_memory:` (`base_react.py:466`) would fail
open — tool-call recording stopping mid-stream with no error.

Because both failures are silent, each SHALL be pinned by a test asserting the positive
outcome — that a request context is visible to the advance, and that run memory set on the
first advance is still visible on the second — rather than by the absence of an exception.

#### Scenario: A tool permission check still sees the request context

- **WHEN** the generator is advanced through a worker during a request that has a Flask
  request context
- **THEN** `has_request_context()` is true inside that advance
- **AND** the RBAC gate evaluates the session's roles instead of taking its
  no-request-context allow-by-default branch

#### Scenario: Run memory survives across advances

- **WHEN** an advance sets a context variable — as `start_run_memory()` does on the first
  advance — and a later advance reads it
- **THEN** the later advance observes the value set by the earlier one

### Requirement: The API reference no longer warns that a stalled provider escapes the deadline

The `client_timeout` row of the chat request-body table in `docs/docs/api_reference.md` SHALL NOT state that the streaming deadline bounds a slow stream but not a stalled provider, and SHALL NOT link issue #191 as an open limitation.

The row SHALL instead state what is now true: a declared deadline bounds how long the client
waits, whether the stream is slow or the provider is silent. Where the page describes the
guarantee, it SHALL be precise that the bound is on client-visible latency — the server may
still be occupied by the abandoned provider call after the 408 — so the text does not
overclaim a cancellation that did not happen.

The code comment at the in-loop check SHALL be corrected in the same way, since it carries
the same claim and the same issue link, and SHALL keep naming its twin check per the
existing cross-reference requirement.

#### Scenario: The stalled-provider caveat is gone from the docs

- **WHEN** an integrator reads the `client_timeout` row after this change
- **THEN** it does not tell them the deadline fails to cover a provider that stalls
- **AND** it carries no issue-#191 link presenting that gap as open

#### Scenario: The bound is described as client-visible latency

- **WHEN** the page states what a declared deadline guarantees
- **THEN** it says the client stops waiting at the deadline
- **AND** it does not claim the server-side provider call is cancelled
