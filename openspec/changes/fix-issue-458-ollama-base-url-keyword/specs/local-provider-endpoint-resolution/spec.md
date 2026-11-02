## ADDED Requirements

### Requirement: An explicit base_url keyword on the Ollama model builder SHALL win over the configured endpoint
In `ollama` mode, the `ChatOllama` client SHALL receive a caller's `base_url` keyword value, not `self.config.base_url`.
That keyword therefore also beats `OLLAMA_HOST`. This matches the `openai_compat`
builder. When the caller passes no `base_url` keyword, the client MUST receive
`self.config.base_url`, including when `extra_kwargs` holds a `base_url` key.

#### Scenario: keyword beats OLLAMA_HOST and the configured base_url
- **WHEN** `OLLAMA_HOST` is `http://ollama-box:11434`, the configured `base_url` is
  `http://configured:11434`, the mode is `ollama`, and the call is
  `_get_ollama_model("m", base_url="http://other:11434")`
- **THEN** `ChatOllama` is constructed with `base_url="http://other:11434"`

#### Scenario: no keyword keeps the resolved endpoint
- **WHEN** the same provider builds a model with `_get_ollama_model("m")`
- **THEN** `ChatOllama` is constructed with `base_url` equal to `provider.config.base_url`

#### Scenario: an extra_kwargs base_url does not beat the configured endpoint
- **WHEN** `extra_kwargs` is `{"local_mode": "ollama", "base_url": "http://extra:1"}` and
  the call passes no `base_url` keyword
- **THEN** `ChatOllama` is constructed with `base_url` equal to `provider.config.base_url`

### Requirement: The provider docs SHALL state the base_url keyword rule
`docs/docs/models_providers.md` SHALL state the keyword rule after the two endpoint precedence lists.
It MUST say that a `base_url=` keyword on the model call beats every entry in those
lists, in both `ollama` and `openai_compat` modes.

#### Scenario: the docs name the keyword rule
- **WHEN** a reader opens the "Local provider endpoint precedence" section
- **THEN** a sentence after the two lists says a `base_url=` keyword on the model call
  wins in both modes
