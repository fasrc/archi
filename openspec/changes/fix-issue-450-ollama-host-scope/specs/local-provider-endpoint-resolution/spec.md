## ADDED Requirements

### Requirement: Environment endpoint override is withheld from openai_compat mode

The `local` provider SHALL withhold the `OLLAMA_HOST` override when its local mode is `openai_compat`, and apply it in every other mode.

`OLLAMA_HOST` names an Ollama daemon. In `openai_compat` mode the provider builds a
`ChatOpenAI` client, so applying that address there points an OpenAI-dialect client at a
server that does not serve the OpenAI route, and the configured host never sees the
request. The override keeps its full effect in `ollama` mode, where the CI smoke path
relies on it.

#### Scenario: A configured openai_compat endpoint outranks the environment

- **WHEN** `OLLAMA_HOST` is set, the local mode is `openai_compat`, and a `base_url` is configured
- **THEN** the provider resolves the configured `base_url` and ignores `OLLAMA_HOST`

#### Scenario: An Ollama-mode endpoint still yields to the environment

- **WHEN** `OLLAMA_HOST` is set, the local mode is `ollama`, and a `base_url` is configured
- **THEN** the provider resolves the `OLLAMA_HOST` value

#### Scenario: An unset or empty variable never changes the endpoint

- **WHEN** `OLLAMA_HOST` is unset or empty and a `base_url` is configured
- **THEN** the provider resolves the configured `base_url` in both local modes

### Requirement: The fallback endpoint matches the local mode

The `local` provider SHALL pick its fallback endpoint from the local mode when no `base_url` is configured.

The two modes speak different dialects on different default ports, so one shared fallback
sends half of the callers to the wrong server. An `openai_compat` provider with no
configured endpoint must not fall back to an Ollama address, whether that address comes
from `OLLAMA_HOST` or from the Ollama default.

#### Scenario: An openai_compat fallback ignores the environment

- **WHEN** the local mode is `openai_compat`, no `base_url` is configured, and `OLLAMA_HOST` is set
- **THEN** the provider resolves `DEFAULT_OPENAI_COMPAT_BASE_URL`

#### Scenario: An openai_compat fallback ignores the Ollama default

- **WHEN** the local mode is `openai_compat`, no `base_url` is configured, and `OLLAMA_HOST` is unset
- **THEN** the provider resolves `DEFAULT_OPENAI_COMPAT_BASE_URL`

#### Scenario: An Ollama fallback still prefers the environment

- **WHEN** the local mode is `ollama`, no `base_url` is configured, and `OLLAMA_HOST` is set
- **THEN** the provider resolves the `OLLAMA_HOST` value

#### Scenario: An Ollama fallback uses the Ollama default

- **WHEN** the local mode is `ollama`, no `base_url` is configured, and `OLLAMA_HOST` is unset
- **THEN** the provider resolves `DEFAULT_OLLAMA_BASE_URL`

### Requirement: The endpoint rule and the client dialect read the local mode the same way

The `local` provider SHALL treat only the exact mode `openai_compat` as OpenAI-compatible, in endpoint resolution and in client construction alike.

`get_chat_model` builds `ChatOpenAI` for the exact string `openai_compat` and
`ChatOllama` for every other value, including `None` and a misspelling. `local_mode`
reaches the provider straight from operator config and no seam validates or
canonicalizes it. So a second, looser test — "anything that is not `ollama`" — would
resolve an openai-compat endpoint for a mode that then gets an Ollama client, and the
client would request an Ollama route against an OpenAI port. One shared predicate keeps
the two answers from drifting.

#### Scenario: An unrecognized mode resolves the Ollama endpoint

- **WHEN** the local mode is `vllm`, no `base_url` is configured, and `OLLAMA_HOST` is unset
- **THEN** the provider resolves `DEFAULT_OLLAMA_BASE_URL`, because that mode builds an Ollama client

#### Scenario: An unrecognized mode still yields to the environment

- **WHEN** the local mode is `vllm` and `OLLAMA_HOST` is set
- **THEN** the provider resolves the `OLLAMA_HOST` value

#### Scenario: A null mode resolves the Ollama endpoint

- **WHEN** `extra_kwargs` carries `local_mode: null`, no `base_url` is configured, and `OLLAMA_HOST` is unset
- **THEN** the provider resolves `DEFAULT_OLLAMA_BASE_URL`

### Requirement: The local mode belongs to the provider, not to the call

The `local` provider SHALL refuse a per-call `local_mode` that disagrees with the mode it was built for.

The endpoint is resolved once, in the constructor, from the stored mode. A per-call mode
that disagreed would pick the other dialect's client and leave that endpoint behind —
the same endpoint-to-client mismatch as above, reached by a second route. No caller in
the repository passes the keyword, so the provider refuses the switch instead of
re-resolving the endpoint per call. The keyword is still consumed, so it cannot reach
the client constructor as an unexpected argument.

#### Scenario: A per-call mode that disagrees is refused

- **WHEN** the provider was built for `openai_compat` and `get_chat_model` is called with `local_mode` of `ollama`
- **THEN** the provider raises `ValueError` naming both the stored mode and the requested mode

#### Scenario: A per-call mode that agrees is accepted

- **WHEN** the provider was built for `openai_compat` and `get_chat_model` is called with `local_mode` of `openai_compat`
- **THEN** the provider builds the OpenAI-dialect client and the keyword does not reach it

#### Scenario: No per-call mode uses the stored mode

- **WHEN** `get_chat_model` is called without a `local_mode` keyword
- **THEN** the provider builds the client for the mode it was constructed with

### Requirement: A resolved endpoint always carries a scheme

The `local` provider SHALL prefix a scheme-less resolved endpoint with `http://`.

Normalization runs after the mode-aware resolution above, so it applies to a configured
value, an `OLLAMA_HOST` value, and a per-mode default alike. A caller that overrides the
endpoint past the provider seam has to apply the same rule.

#### Scenario: A scheme-less configured endpoint is normalized

- **WHEN** the configured `base_url` is `judge-host:8001/v1`
- **THEN** the provider resolves `http://judge-host:8001/v1`
