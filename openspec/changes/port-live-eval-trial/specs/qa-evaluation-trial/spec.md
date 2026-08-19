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

#### Scenario: A forced tool call proves the agent callback port

- **WHEN** the `run` phase executes the fixture's forced-tool static row (its
  question demands a knowledge-base lookup and the agent spec mandates the
  retrieval tool before answering)
- **THEN** `answers.jsonl` contains at least one tool-trace record for that row
  whose tool name is the mandated retrieval tool

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

#### Scenario: Oracle truth is provably isolated from the tested agent

- **WHEN** the trial runs with the oracle serving a distinctive sentinel value
  (via `QA_FAKE_MCP_VALUE_FILE`) and the tested agent has no tool that reaches the
  oracle
- **THEN** every agent-facing artifact in the run workspace — the persisted agent
  configuration, the agent spec, and each attempt's recorded agent input — contains
  no oracle registry alias, no oracle tool name, no recipe field, no sentinel
  value, no resolved-answer provenance string, and no gold-atom text

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

### Requirement: Port inventory accounted and no wholesale copies of shared files

The port SHALL ship a disposition table that assigns every file in the candidate
diff (`git diff --name-status d1c29380 bebfbe56`) exactly one disposition:
`port-verbatim`, `port-hunks`, `skip-unrelated-upstream`, or `skip-dead-on-fork`,
each with a reason. `port-verbatim` MUST be limited to eval-capability files that do
not exist on the fork. A file that exists on the fork MUST receive eval-relevant
hunks only.

#### Scenario: Every candidate file has a disposition

- **WHEN** the disposition table is checked against
  `git diff --name-status d1c29380 bebfbe56`
- **THEN** every listed file appears in the table with exactly one disposition and
  a reason, and no file is unaccounted

#### Scenario: No unrelated upstream content rides the port

- **WHEN** a fork-existing file (for example `static/chat.css` or
  `docs/docs/user_guide.md`) is compared before and after the port
- **THEN** the diff contains only eval-relevant hunks, and none of the pin's
  unrelated upstream-main content (playbooks, A/B testing, Jira docs)

### Requirement: Trial acceptance precedes merge

The implementation PR SHALL NOT merge before (a) the CLI smoke and the console
trial both pass, executed from the PR branch, and (b) a human records the adopt
decision on the tracking issue. A rejected trial SHALL close the PR unmerged.

#### Scenario: Failed trial never lands

- **WHEN** either trial fails and no fix is found on the PR branch
- **THEN** the PR closes unmerged, the dev stack redeploys from `dev`, and the
  writeup records the failure

### Requirement: Existing RAGAS evaluation stack unchanged

The port MUST NOT change the behavior of `archi evaluate`, `archi grade`, the
golden-set maintenance scripts, or their outputs. The two stacks coexist on the
trial branch.

#### Scenario: RAGAS command untouched

- **WHEN** the full unit suite runs on the trial branch
- **THEN** every pre-existing test passes unchanged, including the benchmarking and
  golden-set suites
