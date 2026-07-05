# agent-context-resilience Specification

## Purpose
TBD - created by archiving change harden-benchmark-and-agent-resilience. Update Purpose after archive.
## Requirements
### Requirement: The agent degrades gracefully on a model-context-length overflow

The agent runtime SHALL return a graceful degraded output, marked as a context-overflow result,
when a model call fails because the assembled prompt exceeds the model's context window, rather
than propagating the provider error as an unhandled crash. This mirrors the existing
recursion-limit overflow handling. When no recovery is possible the degraded output MUST carry a
clear, user-appropriate message that the request exceeded the model's context limit. When a
trimmed-context retry recovers an answer, the output MAY carry the recovered answer but MUST
still be marked (via metadata, e.g. `context_overflow_retry`) so downstream consumers do not
treat it as a clean, full-context success.

#### Scenario: invoke() catches a context-length overflow with no recovery

- **WHEN** the underlying model call raises a context-length overflow and no trimmed retry
  recovers an answer
- **THEN** `invoke()` returns a valid degraded pipeline output carrying the context-limit
  message
- **AND** does not raise

#### Scenario: A recovered retry is still marked degraded

- **WHEN** the trimmed-context retry inside the overflow handler succeeds and returns an answer
- **THEN** `invoke()` returns that answer marked with the context-overflow metadata
- **AND** does not raise
- **AND** the result is distinguishable from a clean, full-context answer

#### Scenario: stream() catches a context-length overflow

- **WHEN** the same context-length overflow occurs during a streamed invocation
- **THEN** `stream()` emits a valid degraded terminal output with a context-limit message
- **AND** does not raise

### Requirement: Only genuine context-length overflows are degraded

The context-overflow guard SHALL treat only genuine context-length-exceeded errors as
degradable. Any other provider error — including other HTTP 400s such as malformed parameters —
MUST be re-raised unchanged so real defects continue to surface.

#### Scenario: A malformed-request 400 is not swallowed

- **WHEN** a model call raises a 400 that is not a context-length overflow (e.g. an invalid
  parameter)
- **THEN** the guard re-raises the original error
- **AND** does not return a degraded output

#### Scenario: A context-length 400 is degraded

- **WHEN** a model call raises a 400 whose error body indicates the input token count exceeds
  the model's context length
- **THEN** the guard returns the degraded context-limit output

### Requirement: The overflow detector recognizes OpenAI-compatible context-length errors

The context-overflow detector SHALL recognize the context-length-exceeded error phrasing
emitted by OpenAI-compatible servers (e.g. vLLM), not only the OpenAI-hosted phrasing. In
particular it MUST classify as an overflow the message form "the model's context length is
only N, resulting in a maximum input length of N" produced when the assembled prompt exceeds
the served model's window. Without this, the guard is inert for the very error that motivated
this change.

#### Scenario: vLLM context-length message is detected

- **WHEN** the detector is given the provider error whose message is of the form "You passed
  <N> input tokens ... the model's context length is only <M>, resulting in a maximum input
  length of <M>"
- **THEN** the detector classifies it as a context-overflow error

#### Scenario: Existing OpenAI-hosted phrasing still detected

- **WHEN** the detector is given an error containing `context_length_exceeded` or "maximum
  context length"
- **THEN** the detector still classifies it as a context-overflow error (no regression)

