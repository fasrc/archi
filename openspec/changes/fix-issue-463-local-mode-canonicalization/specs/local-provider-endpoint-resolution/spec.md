## ADDED Requirements

### Requirement: A supported local mode is recognized in any spelling

The `local` provider SHALL match its mode without regard to letter case or surrounding whitespace.

The documentation shows only the two lower-case spellings, but an operator who writes
`OpenAI_Compat` is asking for the OpenAI-compatible client, not for a different provider.
Canonicalization strips surrounding whitespace and lower-cases the value before any
comparison, so one operator intent has one outcome.

#### Scenario: The canonical compat spelling builds the OpenAI client

- **WHEN** the local mode is `openai_compat`
- **THEN** `get_chat_model` builds the OpenAI-dialect client

#### Scenario: A mixed-case compat spelling builds the OpenAI client

- **WHEN** the local mode is `OpenAI_Compat`
- **THEN** `get_chat_model` builds the OpenAI-dialect client

#### Scenario: An upper-case compat spelling builds the OpenAI client

- **WHEN** the local mode is `OPENAI_COMPAT`
- **THEN** `get_chat_model` builds the OpenAI-dialect client

#### Scenario: Surrounding whitespace does not change the mode

- **WHEN** the local mode is ` openai_compat `
- **THEN** `get_chat_model` builds the OpenAI-dialect client

#### Scenario: A mixed-case Ollama spelling builds the Ollama client

- **WHEN** the local mode is `Ollama`
- **THEN** `get_chat_model` builds the Ollama client

### Requirement: An unrecognized local mode is refused before a client is built

The `local` provider SHALL raise `ValueError` when an explicitly set mode is neither `ollama` nor `openai_compat`.

Today an unrecognized value silently selects the Ollama client on the Ollama port, so an
operator who asked for an OpenAI-compatible server learns about it as a request-time 404
on a route that server does not serve. A named error at construction reports the operator's
actual mistake, and mirrors the `provider_type` precedent at
`src/archi/providers/__init__.py:260-266`. A mode is refused before any client is built,
so no request is ever sent to the wrong dialect.

#### Scenario: An unserviceable mode name is refused

- **WHEN** the local mode is `vllm`
- **THEN** the provider raises `ValueError` before building a client

#### Scenario: A near-miss spelling is refused rather than silently downgraded

- **WHEN** the local mode is `openai-compat`
- **THEN** the provider raises `ValueError` before building a client

#### Scenario: An explicitly empty mode is refused

- **WHEN** the local mode is the empty string
- **THEN** the provider raises `ValueError` before building a client

#### Scenario: The error names the rejected value and the valid values

- **WHEN** the provider refuses a mode
- **THEN** the message contains the rejected value, `ollama`, and `openai_compat`

### Requirement: An unset local mode keeps the documented Ollama default

The `local` provider SHALL treat an absent or null mode as `ollama` and SHALL NOT raise for it.

The default is documented and most deployments rely on it. Only an explicitly set
unrecognized value is operator error; saying nothing is not. A config that omits the key
must keep working exactly as it does today.

#### Scenario: A config with no mode key builds the Ollama client

- **WHEN** `extra_kwargs` carries no `local_mode` key
- **THEN** the provider builds the Ollama client and does not raise

#### Scenario: A null mode builds the Ollama client

- **WHEN** `extra_kwargs` carries `local_mode: null`
- **THEN** the provider builds the Ollama client and does not raise

#### Scenario: A null mode reaches the Ollama branch of every mode reader

- **WHEN** `extra_kwargs` carries `local_mode: null` and model discovery or a connection check runs
- **THEN** both take their Ollama branch, the same as an absent key does

### Requirement: Every config seam canonicalizes the operator's mode value

Each seam that copies a `mode` config value into `extra_kwargs` SHALL canonicalize it through the shared whitelist.

Four seams read the operator's `mode` and copy it onward. A whitelist wired to only some
of them leaves the others as silent paths for a bad value, and a whitelist wired inside
each seam's existing truthiness guard would let the empty string through unrefused. Each
seam canonicalizes the raw value before it decides whether to write the key.

#### Scenario: The chat app request seam canonicalizes

- **WHEN** `_build_provider_config_from_payload` reads a local `mode` of `OpenAI_Compat`
- **THEN** the `extra_kwargs` it returns carries `local_mode` of `openai_compat`

#### Scenario: The provider factory seam canonicalizes

- **WHEN** `get_model` reads a local `mode` of `OpenAI_Compat`
- **THEN** the provider config it builds carries `local_mode` of `openai_compat`

#### Scenario: The agent seam canonicalizes

- **WHEN** `_build_provider_config` reads a local `mode` of `OpenAI_Compat`
- **THEN** the config it returns carries `local_mode` of `openai_compat`

#### Scenario: The ingest seam canonicalizes

- **WHEN** `_resolve_provider_config` reads a local `mode` of `OpenAI_Compat`
- **THEN** the config it returns carries `local_mode` of `openai_compat`

#### Scenario: A seam refuses an unrecognized mode

- **WHEN** any of those four seams reads a local `mode` of `vllm`
- **THEN** it raises `ValueError` naming the rejected value and the valid values

#### Scenario: A seam preserves its existing extra_kwargs precedence

- **WHEN** a seam that guards on `local_mode` already being present reads a config carrying both keys
- **THEN** the existing `extra_kwargs` value still wins, unchanged by this requirement

### Requirement: One whitelist serves the provider and the benchmark runner

The explicit-value branch of `resolve_local_mode` SHALL canonicalize through the same whitelist as the provider.

Two independent normalizers drift. The benchmark runner already lower-cases an explicit
mode but accepts any string, so it can hand the provider a value the provider refuses.
Delegating the explicit branch leaves one whitelist in the repository. The runner's
auto-detect branch is unchanged: an empty explicit value still falls through to the URL
convention, which already yields canonical values.

#### Scenario: An explicit mixed-case mode is canonicalized

- **WHEN** `resolve_local_mode` is given an explicit mode of `OpenAI_Compat`
- **THEN** it returns `openai_compat`

#### Scenario: An explicit unrecognized mode is refused

- **WHEN** `resolve_local_mode` is given an explicit mode of `vllm`
- **THEN** it raises `ValueError`

#### Scenario: An empty explicit mode still auto-detects from the URL

- **WHEN** `resolve_local_mode` is given an empty explicit mode and a URL ending in `/v1`
- **THEN** it returns `openai_compat` without raising

### Requirement: The endpoint rule and the client dialect share one string test

The `local` provider SHALL compare the canonical mode against a single shared constant in both endpoint resolution and client construction.

The constructor resolves `config.base_url` from the mode and `get_chat_model` picks the
client from it. Two copies of the comparison drift, and the drift is silent: an endpoint
resolved for one dialect handed to the other dialect's client. One constant, tested in one
predicate, keeps the two answers identical.

#### Scenario: The compat literal is defined once

- **WHEN** the canonicalizer module and the provider module are read together
- **THEN** the `openai_compat` string literal is assigned in exactly one place

#### Scenario: The resolved endpoint matches the client that is built

- **WHEN** the local mode is any accepted spelling of the compat mode and no `base_url` is configured
- **THEN** the provider resolves `DEFAULT_OPENAI_COMPAT_BASE_URL` and builds the OpenAI-dialect client
