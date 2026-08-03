## ADDED Requirements

### Requirement: A failed provider override is never silent

A failed request-time provider/model override SHALL be announced to the caller in band. When a
request supplies a provider/model override and the override does not take effect because the
override LLM could not be constructed, the streaming response MUST carry an event reporting that
outcome. No construction failure may result in a response that is indistinguishable from a
successful override.

An `ImportError` — raised when a provider SDK is absent or broken and is imported lazily during
provider registration — is a construction failure like any other and SHALL be reported. It MUST
NOT be converted into a falsey return value that the call site reads as "no override was
requested".

The two existing outcomes are preserved and are the only permitted ones: a construction-time
`ValueError` ends the stream with `{"type": "error", "status": 400}`, and every other
construction failure emits `{"type": "warning", "message": "Using default model: …"}` and lets
the default pipeline answer.

#### Scenario: An ImportError during construction warns and falls back

- **WHEN** a streaming request supplies a provider and model override
- **AND** constructing the override LLM raises `ImportError` because the provider SDK is
  unavailable
- **THEN** the stream carries a `{"type": "warning", "message": "Using default model: …"}` event
- **AND** the default pipeline answers the request
- **AND** the stream is not terminated by an `error` event

#### Scenario: No override failure produces an unannounced fallback

- **WHEN** the override LLM cannot be constructed for any reason
- **THEN** the caller receives either an `error` event or a `warning` event before the response
  completes
- **AND** the caller can therefore distinguish a fallback answer from an override answer without
  inspecting server logs

### Requirement: Override construction reports failure by raising

The internal helper that constructs an override LLM SHALL signal every failure by raising, and
SHALL NOT return a falsey value to indicate failure. A falsey return is read by the call site's
override guard as "there is no override to apply", which discards the failure rather than
reporting it.

Preserving a provider-specific log message for the `ImportError` case is permitted, provided the
exception is re-raised after logging.

#### Scenario: ImportError propagates to the caller

- **WHEN** the override-construction helper is invoked directly
- **AND** the underlying provider import raises `ImportError`
- **THEN** the helper raises `ImportError` to its caller
- **AND** it does not return `None`

#### Scenario: Successful construction is unaffected

- **WHEN** the override-construction helper is invoked and the provider SDK is available
- **THEN** it returns the constructed chat model
- **AND** no warning or error event is emitted for that request

### Requirement: The hard-rejection path for invalid overrides is unchanged

Making construction failures visible SHALL NOT weaken the existing rejection of invalid or
disabled overrides. A construction-time `ValueError` MUST continue to end the stream with
`{"type": "error", "status": 400}` and MUST NOT be downgraded to a warning with a default-model
answer.

#### Scenario: An invalid provider still ends the stream with 400

- **WHEN** a streaming request supplies an override whose provider does not resolve, or supplies
  an override while overrides are disabled
- **THEN** the stream carries `{"type": "error", "status": 400}`
- **AND** the request returns without an answer from the default pipeline

### Requirement: The documented override outcome table matches the implementation

The API reference's table of override outcomes SHALL NOT describe a silent path that no longer
exists. The row listing "nothing at all — no `error`, no `warning`" MUST stop naming
`ImportError` and falsey construction once construction failures are announced.

The separate silent path in which the active pipeline exposes no `agent_llm` is out of scope and
SHALL remain documented, so the table continues to warn readers about the silent case that is
still real.

#### Scenario: Documentation no longer lists ImportError as silent

- **WHEN** a reader consults the override outcome table in the API reference
- **THEN** `ImportError` and falsey construction are not listed as producing no event
- **AND** the missing-`agent_llm` silent path is still listed
