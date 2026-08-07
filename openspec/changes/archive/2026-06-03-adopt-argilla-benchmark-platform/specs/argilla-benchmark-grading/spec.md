## ADDED Requirements

### Requirement: Argilla server runs alongside archi on the same host

The host SHALL run an Argilla server (default port 6900) and its required ElasticSearch backend as standalone docker containers, distinct from archi's container set. Argilla services SHALL NOT be managed by `archi deploy`; they live in a separate docker-compose project (`argilla/docker-compose.yaml`). ElasticSearch data SHALL persist to `/scratch/docker/volumes/argilla-es/` so that grading data survives restarts.

#### Scenario: Argilla containers present

- **WHEN** the operator runs `docker ps --format '{{.Names}}'` after deploying the platform
- **THEN** the output contains at least `argilla-server` and `argilla-elasticsearch`
- **AND** neither container name appears in any `compose.yaml` under `~/.archi/`

#### Scenario: ElasticSearch data persists across restart

- **WHEN** the operator creates an Argilla dataset, then runs `docker compose down && docker compose up -d` from the `argilla/` directory
- **THEN** the previously created dataset is still listed in Argilla after restart

### Requirement: `archi evaluate` can push benchmark results to Argilla

The archi CLI SHALL provide an `--argilla` flag on `archi evaluate` that, when set, pushes the run's results into a new Argilla dataset at the configured Argilla URL. The dataset name SHALL include a UTC timestamp suffix so multiple runs produce distinct datasets. The Argilla URL, API key, and workspace SHALL be configurable via environment variables (`ARGILLA_API_URL`, `ARGILLA_API_KEY`, `ARGILLA_WORKSPACE`). When `--argilla` is absent, evaluate behavior SHALL be byte-identical to the current behavior (JSON + HTML output only).

#### Scenario: Single-config run pushes to Argilla

- **WHEN** the operator runs `archi evaluate -n test --argilla -c config.yaml -e .secrets.env` against a config with one configuration and 5 questions
- **THEN** an Argilla dataset is created with 5 records
- **AND** each record's fields include `question`, `reference_answer`, `response`, and `trace`
- **AND** each record's metadata includes the four RAGAS scores (if RAGAS mode is enabled) and `time_elapsed`

#### Scenario: A/B sweep run pushes to Argilla as A/B dataset

- **WHEN** the operator runs `archi evaluate -n test --argilla -cd configs/` against a directory of two configs and 5 questions
- **THEN** an Argilla dataset is created with 5 records
- **AND** each record's fields include `question`, `reference_answer`, `answer_a`, `answer_b`, `trace_a`, `trace_b`
- **AND** the record's annotation widgets include `winner: [A, B, Tie]`, `quality: [1-5]`, `notes`

#### Scenario: --argilla absent leaves behavior unchanged

- **WHEN** the operator runs `archi evaluate -n test -c config.yaml -e .secrets.env` without `--argilla`
- **THEN** no Argilla dataset is created
- **AND** the JSON and HTML report files are written to `out_dir` as before

### Requirement: RAGAS judge can be configured independently of the SUT

The `mode_settings.ragas_settings` config block SHALL accept three new keys — `evaluator_provider`, `evaluator_model`, and `evaluator_ollama_url` — that override the corresponding `services.benchmarking.provider`, `model`, and `ollama_url` only for the RAGAS judge LLM. When the new keys are absent, RAGAS judge configuration SHALL fall back to the same values used for the system under test (preserving current behavior).

#### Scenario: Judge differs from SUT

- **GIVEN** a benchmark config with `services.benchmarking.provider: openai` (SUT is local Qwen via OpenAI-compat) and `mode_settings.ragas_settings.evaluator_provider: huit_bedrock` + `evaluator_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- **WHEN** the operator runs `archi evaluate` with RAGAS mode enabled
- **THEN** the SUT generations are produced by local Qwen
- **AND** RAGAS judge calls go to the HUIT Bedrock endpoint

#### Scenario: Judge falls back to SUT when not configured

- **GIVEN** a benchmark config with `services.benchmarking.provider: openai` and no `evaluator_provider` field
- **WHEN** the operator runs `archi evaluate` with RAGAS mode enabled
- **THEN** the RAGAS judge uses the same provider/model as the SUT (matches current pre-change behavior)

### Requirement: HUIT Bedrock is a registered archi provider

A new provider implementation SHALL live at `src/archi/providers/huit_bedrock_provider.py`, registered through the same mechanism as `openai_provider.py`, `anthropic_provider.py`, etc. The provider SHALL hit `{base_url}/model/{model_id}/invoke` with `x-api-key: <HUIT_API_KEY>` and a Bedrock-native Anthropic request body. The provider SHALL be selectable as `provider: huit_bedrock` anywhere a provider is configured (SUT, RAGAS judge, future grader-side LLMs).

#### Scenario: HUIT Bedrock works as SUT provider

- **GIVEN** a config with `services.benchmarking.provider: huit_bedrock` and `model: us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- **WHEN** the operator runs `archi evaluate`
- **THEN** archi answers each question by calling the HUIT Bedrock endpoint

#### Scenario: HUIT Bedrock works as RAGAS judge

- **GIVEN** `mode_settings.ragas_settings.evaluator_provider: huit_bedrock`
- **WHEN** RAGAS evaluation runs
- **THEN** RAGAS metric calls reach the HUIT Bedrock endpoint and return finite float scores

#### Scenario: Missing HUIT_API_KEY fails loudly

- **WHEN** the operator runs `archi evaluate` with HUIT Bedrock configured but no `HUIT_API_KEY` in the environment
- **THEN** the run fails with a clear error message naming the missing secret

### Requirement: Each record must be graded by at least two evaluators

Argilla datasets created by `archi evaluate --argilla` SHALL be configured with `rg.TaskDistribution(min_submitted=<N>)` where `N` is read from the config field `services.benchmarking.argilla.min_submitted` (positive integer, default `2`). A record is not marked complete until at least `N` distinct evaluators have submitted annotations. Analysis tooling SHALL compute inter-rater reliability statistics (Cohen's kappa, Fleiss' kappa) from the multiple-grader records.

#### Scenario: Distribution enforced

- **WHEN** the operator inspects the settings of any dataset created by `archi evaluate --argilla` with `services.benchmarking.argilla.min_submitted` unset
- **THEN** the dataset's task distribution shows `min_submitted: 2`
- **AND** when the same config is set to `services.benchmarking.argilla.min_submitted: 3`, a fresh dataset shows `min_submitted: 3`

#### Scenario: One-grader records remain "pending"

- **GIVEN** an Argilla dataset where 5 records have been graded by only one evaluator
- **WHEN** the operator runs `archi grade --export`
- **THEN** the 5 single-grader records are flagged as incomplete in the exported JSON (e.g., `status: "pending"` or excluded from the "submitted" set)

### Requirement: Config identity is hidden from graders by default

Fields that identify which configuration produced each answer — including `agent_name`, `model`, `provider`, `config_name`, and `git_sha` — SHALL be written to Argilla as `metadata` properties (which are hidden from the grading UI by default), NOT as `fields` (which are displayed). Graders SHALL NOT see this information in the default annotation view.

#### Scenario: Identity in metadata, not fields

- **WHEN** the operator inspects a record in the Argilla UI's annotation view
- **THEN** the displayed fields do not contain the model name, agent name, provider, or config name
- **AND** the corresponding values are present in `metadata` (visible to dataset owners but not to graders by default)

### Requirement: `archi grade` subcommand provides Argilla workflow access

The archi CLI SHALL provide a `archi grade` subcommand with two operations: `--serve` (open the Argilla UI in the browser at the configured URL) and `--export` (pull all submitted annotations from a named dataset and write them to a local JSON file). The subcommand SHALL accept a `--dataset` argument; when omitted, it SHALL resolve the most recent dataset from `~/.archi/.last-benchmark` (written automatically by `archi evaluate --argilla`).

#### Scenario: --serve opens Argilla UI

- **WHEN** the operator runs `archi grade --serve`
- **THEN** the operator's default browser opens to the configured Argilla URL's `/datasets` page

#### Scenario: --export writes grades to JSON

- **GIVEN** an Argilla dataset with 5 records and 10 submitted annotations
- **WHEN** the operator runs `archi grade --export --dataset <name> --output grades.json`
- **THEN** `grades.json` contains one entry per question, each with a `responses` list of the submitted annotations (winner, quality, notes, user_id, etc.)

#### Scenario: --export uses last-benchmark when --dataset omitted

- **GIVEN** `~/.archi/.last-benchmark` exists with a recent `dataset_name`
- **WHEN** the operator runs `archi grade --export` without `--dataset`
- **THEN** the named dataset from the state file is used and a clear message is printed identifying which dataset was selected

### Requirement: Configs being compared run in a single sweep

When two or more configurations are being compared in a primary analysis, they MUST be run together in a single `archi evaluate -cd <directory>` invocation, not as separate invocations. The analysis tooling SHALL refuse to compute primary outcome statistics across configs that were not run in the same sweep, and SHALL surface the reason (different `corpus_snapshot_id` values).

#### Scenario: Sweep guarantees same corpus

- **WHEN** the operator runs `archi evaluate -cd configs/` containing two config files
- **THEN** both configs share the same vectorstore ingestion state during the run
- **AND** the metadata of every record from both configs carries the same `corpus_snapshot_id`

#### Scenario: Analysis rejects cross-sweep comparison

- **GIVEN** two Argilla datasets produced by separate `archi evaluate` invocations with different `corpus_snapshot_id` values
- **WHEN** the analysis notebook is asked to compute a per-config primary outcome comparing them
- **THEN** the comparison is refused with a clear message naming the differing `corpus_snapshot_id` values

### Requirement: Anchor questions are present in every run

Every run SHALL include a curated set of anchor questions (sourced from `config/benchmarking/anchor_questions.json`) interleaved with the regular test questions. Anchor questions SHALL appear to graders as ordinary records (NO visible "anchor" marker in the UI). The analysis tooling SHALL separately report anchor-question outcomes per config so that a regression on a known-good anchor blocks adoption of a new config.

#### Scenario: Anchors merged at run time

- **GIVEN** `queries.json` contains 50 questions and `anchor_questions.json` contains 5
- **WHEN** the operator runs `archi evaluate`
- **THEN** the run processes 55 questions total
- **AND** the anchor questions are not marked as anchors in any field visible to the grader

#### Scenario: Anchor regression blocks adoption

- **GIVEN** an analysis where v1-strict answered all 5 anchors correctly and v2-lean answered 4 anchors correctly
- **WHEN** the analysis tooling computes the adoption decision against the pre-registered decision rule
- **THEN** the rule "no anchor question regressed v1 → v2" is reported as failed and v2 is not recommended for adoption regardless of the primary outcome

### Requirement: Pre-registration is committed before grading opens

Every grading round SHALL be preceded by a committed pre-registration file at `docs/eval/preregs/<YYYY-MM-DD>-<study-slug>.md` containing the primary hypothesis, primary outcome metric, statistical test, decision rule, secondary analyses (flagged as exploratory), and stopping rule. The pre-reg SHALL be referenced by name in the eventual analysis writeup. A template at `docs/eval/preregs/_template.md` SHALL exist as the starting point.

#### Scenario: Template exists

- **WHEN** the operator runs `ls docs/eval/preregs/_template.md`
- **THEN** the file exists

#### Scenario: Pre-reg referenced in writeup

- **WHEN** the operator publishes an eval-round writeup for round X
- **THEN** the writeup references a specific pre-reg file by path or filename, and the referenced file exists in the repo's git history at a commit predating the first grade submission

### Requirement: A/B preference is the primary grading shape for config comparison

When two configs are being compared, the Argilla dataset SHALL use the A/B preference schema (`winner: [A, B, Tie]` as the required label question, plus `quality` rating of winner and free-text `notes`). Single-config absolute-quality grading uses the single-config schema (correctness label, failure-mode multi-label, quality rating, notes). The two schemas are mutually exclusive within a single dataset.

#### Scenario: Two-config sweep uses A/B schema

- **WHEN** the operator runs `archi evaluate --argilla -cd configs/` against two configs
- **THEN** the resulting dataset's annotation widgets include `winner: [A, B, Tie]`, `quality: [1-5]`, and `notes`

#### Scenario: Single-config run uses absolute schema

- **WHEN** the operator runs `archi evaluate --argilla -c single.yaml` against one config
- **THEN** the resulting dataset's annotation widgets include `correctness: [correct, partial, incorrect]`, `failure_modes: multi-select`, `quality: [1-5]`, and `notes`

### Requirement: RAGAS end-to-end smoke test passes before relying on scores

A smoke test SHALL exist at `tests/smoke/ragas_smoke.py` that runs `archi evaluate` on a 3-question subset with RAGAS mode enabled and the configured judge provider, and asserts that every record receives finite (non-NaN, non-None) float values for all four RAGAS metrics (`answer_relevancy`, `faithfulness`, `context_precision`, `context_recall`). This smoke test SHALL pass before any documentation or analysis depends on RAGAS metric values.

#### Scenario: Smoke test exists and passes

- **WHEN** the operator runs `python tests/smoke/ragas_smoke.py`
- **THEN** the script exits 0
- **AND** the test output reports `3/3 records have finite values for all four RAGAS metrics`

#### Scenario: Smoke test surfaces the `TODO likely broken now` regression

- **GIVEN** an environment where the upstream RAGAS bug from `service_benchmark.py:503,548` is reintroduced
- **WHEN** the operator runs `python tests/smoke/ragas_smoke.py`
- **THEN** the script exits non-zero with a clear error identifying which RAGAS metrics returned non-finite values
