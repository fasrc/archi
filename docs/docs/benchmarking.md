# Benchmarking

Archi provides benchmarking functionality via the `archi evaluate` CLI command to measure retrieval and response quality.

## Evaluation Modes

Two modes are supported (can be used together):

### SOURCES Mode

Checks if retrieved documents contain the correct sources by comparing metadata fields.

- Default match field: `file_name` (configurable per-query)
- Override with `sources_match_field` in the queries file

### RAGAS Mode

Uses the [Ragas](https://docs.ragas.io/en/stable/concepts/metrics/) evaluator for four metrics:

- **Answer relevancy**: How relevant the answer is to the question
- **Faithfulness**: Whether the answer is grounded in the retrieved context
- **Context precision**: How relevant the retrieved documents are
- **Context relevancy**: How much of the retrieved context is useful

---

## Preparing the Queries File

Provide questions, expected answers, and correct sources in JSON format:

```json
[
  {
    "question": "Does Jorian Benke work with the PPC?",
    "sources": [
      "https://ppc.mit.edu/blog/2025/07/14/welcome-our-first-ever-in-house-masters-student/",
      "CMSPROD-42"
    ],
    "answer": "Yes, Jorian works with the PPC and her topic is Lorentz invariance.",
    "source_match_field": ["url", "ticket_id"]
  }
]
```

| Field | Required | Description |
|-------|----------|-------------|
| `question` | Yes | The question to ask |
| `sources` | Yes | List of source identifiers (URLs, ticket IDs, etc.) |
| `answer` | Yes | Expected answer (used for RAGAS evaluation) |
| `source_match_field` | No | Metadata fields to match sources against (defaults to config value) |

See `examples/benchmarking/queries.json` for a complete example.

---

## Title-Aware Query Set

To measure [title-aware retrieval](data_sources.md#re-ingesting-to-backfill-title-aware-search-text)
(the `title_weight`, `filename_boost`, and `title_header` knobs), the harness appends a
curated query set whose keyword appears **only** in the document title (`display_name`) or
filename (`file_name`) — never in the chunk body. This lets a before/after run report the
recall change for queries that body-only indexing would miss.

The query set ships at `src/bin/benchmark_query_sets/title_aware_query_set.json` and is
merged into your deployment's queries automatically. Each item carries:

| Field | Description |
|-------|-------------|
| `question` | The title-only or filename-only keyword query |
| `category` | `title_only` or `filename_only` |
| `sources` | Source identifiers expected to be retrieved |
| `source_match_field` | `display_name` for `title_only`, `file_name` for `filename_only` |

The merge is controlled by the `BENCH_INCLUDE_TITLE_AWARE` environment variable
(default `true`). Set it to `false` (or `0`/`no`/`off`) to benchmark your own query set
only:

```bash
BENCH_INCLUDE_TITLE_AWARE=false archi evaluate -n benchmark -c config.yaml -e .secrets.env
```

To compare retrieval quality before and after enabling title-aware retrieval, run the
benchmark with the feature off and on (toggling `title_header.enabled`, `title_weight`,
and `filename_boost` in config) and compare the SOURCES-mode recall on the
`title_only`/`filename_only` queries.

---

## Configuration

```yaml
services:
  benchmarking:
    agent_class: CMSCompOpsAgent
    agent_md_file: examples/agents/cms-comp-ops.md
    provider: local
    model: qwen3:32b
    ollama_url: http://host.containers.internal:7870
    queries_path: examples/benchmarking/queries.json
    out_dir: bench_out
    modes:
      - "RAGAS"
      - "SOURCES"
    mode_settings:
      sources:
        default_match_field: ["file_name"]
      ragas_settings:
        embedding_model: OpenAI
```

| Key | Default | Description |
|-----|---------|-------------|
| `agent_class` | — | Pipeline/agent class to run for benchmark questions |
| `agent_md_file` | — | Path to a single agent markdown file |
| `provider` | — | Provider used for benchmark question answering |
| `model` | — | Model used for benchmark question answering |
| `ollama_url` | — | Ollama base URL when `provider: local` |
| `queries_path` | — | Path to the queries JSON file |
| `out_dir` | — | Output directory for results (must exist) |
| `modes` | — | List of evaluation modes (`RAGAS`, `SOURCES`) |
| `mode_settings.ragas_settings.timeout` | `180` | Max seconds per QA pair for RAGAS evaluation |
| `mode_settings.ragas_settings.batch_size` | Ragas default | Number of QA pairs to evaluate at once |

`archi evaluate` now requires benchmark runtime fields under `services.benchmarking`.
`services.chat_app` fields are not used for benchmark runtime configuration.

### RAGAS Settings

| Key | Description |
|-----|-------------|
| `embedding_model` | `OpenAI` or `HuggingFace` |

---

## Running

Evaluate one or more configurations:

```bash
# Single config file
archi evaluate -n benchmark -c config.yaml -e .secrets.env

# Directory of configs (for comparing hyperparameters)
archi evaluate -n benchmark -cd configs/ -e .secrets.env

# With GPU support
archi evaluate -n benchmark -c config.yaml -e .secrets.env --gpu-ids all
```

Make sure the `out_dir` exists before running.

---

## Results

Results are saved in a timestamped subdirectory of `out_dir` (e.g., `bench_out/2042-10-01_12-00-00/`).

To analyze results, see `scripts/benchmarking/` which contains:

- Plotting functions
- An IPython notebook with usage examples (`benchmark_handler.ipynb`)
