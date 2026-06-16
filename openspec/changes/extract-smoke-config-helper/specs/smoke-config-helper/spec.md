## ADDED Requirements

### Requirement: YAML value retrieval via dotted path
The helper SHALL provide a `get` subcommand that reads a YAML file and prints a single value located at a dot-separated key path to stdout.

#### Scenario: Read a nested scalar value
- **WHEN** invoked as `smoke_config_helper.py get <file> services.postgres.host --default localhost`
- **THEN** the helper prints the value at `cfg["services"]["postgres"]["host"]`, or `localhost` if any key in the path is missing

#### Scenario: Read an array element by index
- **WHEN** invoked as `smoke_config_helper.py get <file> services.chat_app.providers.local.models.0`
- **THEN** the helper prints `cfg["services"]["chat_app"]["providers"]["local"]["models"][0]`

#### Scenario: Missing key without default
- **WHEN** invoked as `smoke_config_helper.py get <file> nonexistent.path` with no `--default` flag
- **THEN** the helper prints an empty string and exits with code 0

#### Scenario: File does not exist
- **WHEN** the specified YAML file does not exist
- **THEN** the helper exits with a non-zero exit code and prints an error to stderr

---

### Requirement: Ollama provider config writing
The helper SHALL provide a `set-ollama` subcommand that updates a YAML config file with ollama provider settings under `services.chat_app.providers.local`.

#### Scenario: Set ollama model and URL
- **WHEN** invoked as `smoke_config_helper.py set-ollama <file> --model qwen2.5:0.5b --url http://localhost:11434`
- **THEN** the helper writes the model as the first entry in `models`, sets `default_model`, sets `base_url`, sets `enabled: true`, and sets `chat_app.default_provider` to `local` and `chat_app.default_model` to the model name

#### Scenario: Partial arguments
- **WHEN** invoked with `--model` but without `--url`
- **THEN** the helper updates only the model fields and leaves `base_url` unchanged

---

### Requirement: vLLM provider config writing
The helper SHALL provide a `set-vllm` subcommand that updates a YAML config file with vLLM provider settings under `archi.providers.vllm`.

#### Scenario: Set vLLM base URL and model
- **WHEN** invoked as `smoke_config_helper.py set-vllm <file> --base-url http://localhost:8000/v1 --model Qwen/Qwen2.5-0.5B`
- **THEN** the helper sets `enabled: true`, `base_url`, `default_model`, and `models: [<model>]` under `archi.providers.vllm`

#### Scenario: Model omitted
- **WHEN** invoked with `--base-url` but without `--model`
- **THEN** the helper sets `enabled` and `base_url` but does not modify `default_model` or `models`

---

### Requirement: Agent spec validation
The helper SHALL provide a `validate-agents` subcommand that loads and validates all agent spec markdown files from the configured `agents_dir`.

#### Scenario: Valid agents directory
- **WHEN** invoked as `smoke_config_helper.py validate-agents <config-file> --repo-root /path/to/repo`
- **THEN** the helper reads `agents_dir` from the config, dynamically imports `agent_spec.py` from `<repo-root>/src/archi/pipelines/agents/`, lists all `.md` agent files, calls `load_agent_spec()` on each, and exits with code 0

#### Scenario: agents_dir missing from config
- **WHEN** the config file does not contain `services.chat_app.agents_dir`
- **THEN** the helper exits with a non-zero code and prints an error to stderr

#### Scenario: agents_dir has no markdown files
- **WHEN** the directory exists but contains no `.md` files
- **THEN** the helper exits with a non-zero code and prints an error to stderr

#### Scenario: agent_spec.py not found
- **WHEN** `<repo-root>/src/archi/pipelines/agents/agent_spec.py` does not exist
- **THEN** the helper exits with a non-zero code and prints an error to stderr

---

### Requirement: Environment file value retrieval
The helper SHALL provide an `env-get` subcommand that reads a `.env` file and prints the value for a given key.

#### Scenario: Key exists in env file
- **WHEN** invoked as `smoke_config_helper.py env-get <file> PG_PASSWORD`
- **THEN** the helper prints the value after the first `PG_PASSWORD=` line, stripped of leading/trailing whitespace

#### Scenario: Key does not exist
- **WHEN** the key is not found in the file
- **THEN** the helper prints an empty string and exits with code 0

#### Scenario: Env file does not exist
- **WHEN** the specified file does not exist
- **THEN** the helper exits with a non-zero code and prints an error to stderr

---

### Requirement: Shell script uses helper exclusively
`scripts/dev/run_smoke_preview.sh` SHALL contain zero inline Python heredoc blocks (`<<'PY'`). All Python operations MUST be performed via calls to `scripts/dev/smoke_config_helper.py`.

#### Scenario: No heredocs remain
- **WHEN** the shell script is searched for the pattern `<<'PY'` or `<<PY`
- **THEN** zero matches are found

#### Scenario: Behavioral parity
- **WHEN** the refactored shell script is run with identical inputs to the pre-refactor version
- **THEN** it produces the same environment variables, config file mutations, and exit codes
