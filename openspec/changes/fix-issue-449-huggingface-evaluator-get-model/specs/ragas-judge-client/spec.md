## ADDED Requirements

### Requirement: The huggingface evaluator provider returns a working judge client

The benchmark harness SHALL return a usable RAGAS judge client whenever the resolved evaluator provider is `huggingface`, and SHALL NOT raise while building it.

`huggingface` names a serving convention, not a library: it means an OpenAI-compatible
endpoint of the kind vLLM and TGI expose. The client SHALL therefore be the OpenAI-compatible
one, built through the same provider seam and the same `openai_compat` mode the `local`
provider uses for a `/v1` endpoint, so that one judge-construction path serves both.

The provider SHALL be resolved from `mode_settings.ragas_settings.evaluator_provider` when
that key carries a value, and from `services.benchmarking.provider` when it does not. Both
routes reach this requirement; `evaluator_provider` is rendered into every generated
deployment config, so it is an operator surface and not internal state.

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

#### Scenario: The system-under-test provider key reaches the same arm

- **WHEN** no `evaluator_provider` is configured and `services.benchmarking.provider` is `huggingface`
- **THEN** the harness returns an OpenAI-compatible chat client bound to the system-under-test model
- **AND** no exception is raised while building it

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
