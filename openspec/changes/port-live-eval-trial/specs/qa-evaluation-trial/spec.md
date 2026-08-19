## ADDED Requirements

### Requirement: Ported QA-evaluation CLI runs all phases

The fork SHALL provide the upstream `archi eval qa` command, ported verbatim from
the pinned upstream commit, with `prepare`, `run`, and `score` subcommands and the
composite single-command mode. The `run` phase MUST execute the fork's own agent
pipeline (`BaseReActAgent`) in-process, so a trial evaluates this fork's agent and
not upstream's.

#### Scenario: Staged phases complete against the trial fixtures

- **WHEN** `archi eval qa prepare` runs on `examples/qa_eval/dataset.json` with the
  trial evaluator profile and the CLI MCP registry, followed by `run` (agent config
  + agent spec, 2 attempts) and `score` on the same output directory
- **THEN** each phase exits 0
- **AND** the workspace manifest reaches status `scored`
- **AND** `report.md` is written and no row is a failed row

#### Scenario: Composite mode matches the staged phases

- **WHEN** `archi eval qa` runs with the same dataset, profile, registry, agent
  config, and agent spec against a fresh output directory
- **THEN** it exits 0 and produces a `scored` manifest in one invocation

#### Scenario: Tool traces prove the agent callback port

- **WHEN** the tested agent uses at least one tool during the `run` phase
- **THEN** `answers.jsonl` contains tool-trace records for that attempt

### Requirement: Live rows resolve through an evaluator-only MCP oracle

Dataset V2 live items SHALL be resolved through the evaluator MCP registry passed
with `--mcp-config`. The tested agent's configuration MUST NOT receive any oracle
server, recipe, resolved truth, or gold atoms.

#### Scenario: Live rows materialize from the fake oracle

- **WHEN** `prepare` runs on a dataset with 2 live items whose recipes call the
  bundled fake MCP server's `current_capacity` tool over stdio
- **THEN** both live rows resolve, and the prepared records carry the
  oracle-resolved answer and its provenance

#### Scenario: Changed live truth changes the resolved answer

- **WHEN** `QA_FAKE_MCP_VALUE_FILE` points at a file containing `9` and `prepare`
  re-runs into a fresh output directory
- **THEN** the resolved live answer reflects capacity 9, not the default 7

### Requirement: Evaluations console behind a config toggle

The chat app SHALL expose the ported `/evaluations` console only when
`services.chat_app.evaluations.enabled` is strictly `true` in the deployed
configuration. All console configuration parsing and request authorization SHALL
live in a unit-tested seam module (`evaluation_console.py`); `app.py` SHALL contain
thin call sites only.

#### Scenario: Console off by default

- **WHEN** the deployed configuration omits the `evaluations` block or sets
  `enabled` to anything but boolean `true`
- **THEN** `/evaluations` is not registered and the index page shows no
  evaluations nav link

#### Scenario: Console on for the trial deployment

- **WHEN** the dev deployment sets `evaluations.enabled: true` with a staged
  `mcp_config_path` and is redeployed
- **THEN** `GET /evaluations` returns 200, the nav link renders, and a full console
  loop (import dataset → import profile → generate and approve atoms → run → score
  → history) completes

#### Scenario: Auth-off deployments authorize all console requests

- **WHEN** the deployment runs with auth disabled (the FASRC dev configuration)
- **THEN** the seam's `authorize_request` allows console requests without a session

### Requirement: Evaluation MCP registry staging at deploy time

`archi create` SHALL treat a configured
`services.chat_app.evaluations.mcp_config_path` as a host source path: validate it,
stage it into the generated `evaluation_config/` directory, mount it read-only into
the chatbot container, and rewrite the running config's `mcp_config_path` to the
fixed container path.

#### Scenario: Registry staged and mounted

- **WHEN** the deploy config sets `mcp_config_path` to a valid registry file and
  `archi create` renders the deployment
- **THEN** the file is copied to `evaluation_config/qa_evaluation_mcp.yaml`, the
  compose file mounts that directory read-only, and the rendered running config
  points at `/root/archi/evaluation_config/qa_evaluation_mcp.yaml`

#### Scenario: No registry, no mount

- **WHEN** the deploy config leaves `mcp_config_path` unset
- **THEN** no `evaluation_config/` staging occurs and the compose file has no
  evaluation-config mount

### Requirement: Existing RAGAS evaluation stack unchanged

The port MUST NOT change the behavior of `archi evaluate`, `archi grade`, the
golden-set maintenance scripts, or their outputs. The two stacks coexist on the
trial branch.

#### Scenario: RAGAS command untouched

- **WHEN** the full unit suite runs on the trial branch
- **THEN** every pre-existing test passes unchanged, including the benchmarking and
  golden-set suites
