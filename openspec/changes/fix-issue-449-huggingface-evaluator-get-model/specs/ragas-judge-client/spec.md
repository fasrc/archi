## ADDED Requirements

### Requirement: The huggingface evaluator provider returns a working judge client

The benchmark harness SHALL return a usable RAGAS judge client whenever the resolved evaluator provider is `huggingface`, and SHALL NOT raise while building it.

`huggingface` names a serving convention, not a library: it means an OpenAI-compatible
endpoint of the kind vLLM and TGI expose. The client SHALL therefore be the OpenAI-compatible
one, built through the same provider seam and the same `openai_compat` mode the `local`
provider uses for a `/v1` endpoint, so that one judge-construction path serves both.

The provider SHALL be resolved from `mode_settings.ragas_settings.evaluator_provider` when
that key carries a value, and from `services.benchmarking.provider` when it does not.
`evaluator_provider` is rendered into every generated deployment config, so it is an
operator surface and not internal state.

`huggingface` is an evaluator-only provider name, and the spec SHALL NOT claim the
system-under-test key reaches this arm. `ProviderType` has no `huggingface` member, and
`load_new_configuration` builds the system under test through `archi(...)` before any judge
exists, so `services.benchmarking.provider: huggingface` raises at pipeline construction.
The resolution order above is shared by every evaluator arm and stays as it is; what does
not exist is a deployment that arrives here by that route.

The judge model SHALL come from `mode_settings.ragas_settings.evaluator_model`, falling back
to `services.benchmarking.model`. The fallback exists so an operator can point the judge at a
different endpoint without restating the model.

#### Scenario: A configured judge endpoint is used

- **WHEN** `evaluator_provider` is `huggingface` and `evaluator_ollama_url` is `http://judge-host:8001/v1`
- **THEN** the harness returns an OpenAI-compatible chat client whose base URL is `http://judge-host:8001/v1`
- **AND** no exception is raised while building it

#### Scenario: The judge model is taken from the evaluator key

- **WHEN** `evaluator_provider` is `huggingface`, `evaluator_model` is `judge-x`, and the system under test is configured with a different model
- **THEN** the returned client is bound to `judge-x` rather than to the system-under-test model

#### Scenario: The system-under-test provider name is rejected earlier

- **WHEN** `services.benchmarking.provider` is `huggingface`
- **THEN** building the system under test raises, because `ProviderType` has no such member
- **AND** the judge arm is reachable only through `evaluator_provider`

### Requirement: An unconfigured huggingface judge falls back to the local OpenAI-compatible port

The harness SHALL default the `huggingface` judge endpoint to `http://localhost:8000/v1` when neither the judge URL nor the system-under-test URL is configured.

A configured URL SHALL win over the default, and the judge URL SHALL be resolved from
`mode_settings.ragas_settings.evaluator_ollama_url` first and from
`services.benchmarking.ollama_url` second. Inheriting the system-under-test URL is deliberate:
an operator running one endpoint for both roles configures it once.

The arm SHALL treat the endpoint as OpenAI-compatible without consulting the URL. Deciding
the mode from the URL would resolve an unconfigured judge to a native Ollama client, because
the shared resolver reads an absent URL as an empty string and an empty string does not end in
`/v1`. That would move the failure from construction time to scoring time and contradict the
meaning of the provider name.

This requirement governs which client is built, not whether the endpoint answers. A judge URL
that names a native Ollama port still yields an OpenAI-compatible client, which will not be
served; refusing or auto-detecting that case is out of scope here.

#### Scenario: No URL is configured anywhere

- **WHEN** `evaluator_provider` is `huggingface` and neither `evaluator_ollama_url` nor `services.benchmarking.ollama_url` is set
- **THEN** the returned client's base URL is `http://localhost:8000/v1`

#### Scenario: The system-under-test URL is inherited

- **WHEN** `evaluator_provider` is `huggingface`, no `evaluator_ollama_url` is set, and `services.benchmarking.ollama_url` is `http://sut-host:9000/v1`
- **THEN** the returned client's base URL is `http://sut-host:9000/v1` rather than the default

#### Scenario: The mode is not inferred from the endpoint

- **WHEN** the `huggingface` judge endpoint is resolved to the default, whose host is local and whose path ends in `/v1`
- **THEN** the client is OpenAI-compatible because the provider says so, not because the URL was inspected
- **AND** the other evaluator providers keep the client each of them builds today

### Requirement: The resolved judge endpoint outranks OLLAMA_HOST

The judge client SHALL be bound to the endpoint this capability resolves, and SHALL NOT be redirected to the system under test by the `OLLAMA_HOST` environment variable.

`load_new_configuration` exports the system-under-test URL as `OLLAMA_HOST`, and
`LocalProvider` replaces even an explicitly supplied `base_url` with that variable. Passing
the endpoint only inside the provider configuration therefore loses it: the judge silently
scores against the system under test, which either fails because the judge model is not
served there or invalidates the run by making the system under test its own judge. The
second outcome is the dangerous one, because it produces numbers rather than an error.

The endpoint SHALL therefore be supplied where the provider seam cannot rewrite it. This
applies to every arm that builds its client through that seam, not only to `huggingface`.

An override SHALL be supplied only when an endpoint was resolved. Where no judge URL and no
system-under-test URL are configured, the arm SHALL keep whatever endpoint the provider
selects. Overriding with an empty value would erase the provider's local default and leave
the client pointing at the public OpenAI endpoint, which is a worse outcome than the
redirection this requirement exists to prevent: the `huggingface` arm cannot reach that
state because it resolves a default of its own first, but the `local` arm can.

#### Scenario: A configured judge endpoint outranks the exported system-under-test host

- **WHEN** `OLLAMA_HOST` names the system-under-test endpoint and `evaluator_ollama_url` names a different judge endpoint
- **THEN** the returned client's base URL is the judge endpoint

#### Scenario: The default judge endpoint outranks the exported system-under-test host

- **WHEN** `OLLAMA_HOST` names the system-under-test endpoint and no judge URL and no system-under-test URL are configured
- **THEN** the returned client's base URL is `http://localhost:8000/v1`

#### Scenario: An arm with no default of its own keeps the provider's endpoint

- **WHEN** the `local` arm resolves to `openai_compat` and no judge URL and no system-under-test URL are configured
- **THEN** the returned client's base URL is the one the provider selects
- **AND** the base URL is never empty
