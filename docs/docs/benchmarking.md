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
- `analyze_grades.ipynb` — for runs that pushed human grades to Argilla (see below)

---

## Prompt sweep

A *prompt sweep* runs N agent prompts through the RAGAS harness with everything
but the prompt held fixed — model, queries, retriever, and the RAGAS judge all
come from one base config — and ranks the prompts on a **leaderboard** by mean
RAGAS metric. It generalizes a 2-way A/B to the whole prompt field, which is how
we settle "which support prompt do we ship?" with data rather than by eyeballing
one pair at a time (the open question Q5 / Decision 3 in
`docs/docs/notes_response_tuning.md`).

Only `services.benchmarking.agent_md_file` varies across variants. The model and
RAGAS judge are deliberately held fixed; the leaderboard's `shared_context`
cross-checks this and flags any drift.

### 1. Write a manifest

A manifest names one base config and the prompts to sweep. See
`config/benchmarking/prompt_sweep.yaml` for a worked example:

```yaml
base_config: config/benchmarking/ragas.yaml   # supplies model/queries/judge/retriever
out_dir: bench_out/sweep_configs              # optional (this is the default)
primary_metric: faithfulness                  # leaderboard sort key (optional)
prompts:
  - config/agents/fasrc-cannon-v1-strict.md
  - config/agents/fasrc-cannon-v2-lean.md
  - config/agents/fasrc-cannon-v3-cited.md
  - config/agents/fasrc-cannon-v4-linked.md
```

`primary_metric` is one of `answer_relevancy`, `faithfulness`,
`context_precision`, `context_recall` (default `faithfulness` — grounding is the
load-bearing property for a "never guess" support bot). All four metrics are
reported per variant regardless; this only sets the ranking key.

### 2. Generate the per-prompt configs

```bash
python scripts/benchmarking/generate_prompt_sweep.py -m config/benchmarking/prompt_sweep.yaml
```

This writes one config per prompt into the sweep directory, each identical to
the base except `services.benchmarking.agent_md_file` (the prompt) and `.name`
(the prompt's filename stem). It refuses to write anything if a prompt path is
missing, so a bad manifest never leaves a partial set behind.

### 3. Run the sweep

```bash
archi evaluate --config-dir bench_out/sweep_configs --hostmode
```

The harness runs each config in turn (the existing `--config-dir` path) and,
because 2+ configs ran, emits a `leaderboard` block in the dump JSON.

### Reading the leaderboard

The dump JSON gains a `leaderboard` key:

- `rows` — one per variant: `name`, `agent_md_file`, the four mean RAGAS
  `metrics`, `primary_score`, `rank`, `query_count`, and `incomplete`. Rows are
  ranked best-first by `primary_metric`; ties share a rank. A variant that
  failed to produce a metric (missing/NaN) has `None` for it, is marked
  `incomplete: true`, and sorts after all complete variants — it is never
  treated as a zero.
- `shared_context` — the model, provider, judge `evaluator_model`,
  `queries_path`, and `corpus_snapshot_id` shared by all variants. If any of
  these differ across the swept configs, the discrepancy is recorded in
  `shared_context.warnings` (the sweep is no longer apples-to-apples).

The pairwise `ab_comparisons` are still produced alongside the leaderboard; the
leaderboard is computed independently from each config's aggregates.

---

## Hierarchical-rerank A/B

A two-arm benchmark that measures what the hierarchical-rerank retriever buys
over the baseline — across **three** deltas: answer quality (RAGAS), per-query
latency, and deployment image size. The config pair and grounded question banks
live under `examples/benchmarking/hierarchical_rerank_ab/` (see its `README.md`
for the full A/B contract).

- **Baseline arm** — `CharacterTextSplitter` (flat `character` chunking) +
  `HybridRetriever`.
- **Treatment arm** — `sentence` hierarchical chunking + the hierarchical-rerank
  retriever (FlashRank rerank returning parent context).

Only the chunking strategy, retriever selection, arm name, and data path differ
between the two configs. The embedding model, candidate-generation weights
(`bm25_weight`/`semantic_weight`), system-under-test model, RAGAS judge, and
question bank are held identical, so any measured difference is attributable to
the retrieval treatment. The leaderboard's `shared_context.warnings` cross-checks
this. Each arm uses a **distinct `global.DATA_PATH`** so the two vectorstores do
not collide.

### Run

```bash
archi evaluate -n hr-ab -cd examples/benchmarking/hierarchical_rerank_ab --hostmode
```

`--name`/`-n` is required by the `evaluate` CLI (it names the deployment).

Both arms ingest their own corpus, then answer the shared bank. The dump JSON
gains `ab_comparisons` (baseline vs treatment) plus a `leaderboard`.

### Measuring the three deltas

- **Quality** — the four RAGAS metrics per arm, reported overall and (using the
  typed `fasrc_ragas_queries.json` bank) sliced by `anchor_type`
  (`easy_retrieve` / `reasoning` / `should_refuse`). The hypothesis is that
  returning parent context helps multi-step `reasoning` questions more than
  simple lookups.
- **Latency** — the treatment's **first** query pays a one-time FlashRank ONNX
  model load (~45s on dev vs ~8s baseline). Report that cold-load cost
  separately and compute **warm** latency by excluding the first treatment query
  (or prepend a throwaway warm-up question), so the steady-state number is honest.
- **Image size** — record the built deployment image size with and without the
  treatment's dependencies (`llama-index-core` + `flashrank`); the difference is
  the image-size delta the feature introduces.

### Sweeping chunk sizes

The hierarchical parent/child target sizes are configurable via
[`data_manager.chunking.parent_chunk_size`/`child_chunk_size`](configuration.md#chunking).
To recommend defaults from data rather than assumption, clone the treatment
config into variants that differ **only** in those two keys (e.g. 1024/256,
2048/512, 4096/512) and run them as one config directory — the leaderboard ranks
them. The same pattern sweeps `retrievers.hybrid_retriever.bm25_weight`.

---

## Human grading via Argilla

`archi evaluate --argilla` pushes benchmark results to a self-hosted [Argilla](https://argilla.io/) instance for independent human grading. This is the platform we use to answer the question "is config A better than config B for FASRC users?" with data we trust — RAGAS scores alone can't decide prompt or model choices because the judge LLM has its own biases.

### Operator loop

```
1. Edit questions in   config/benchmarking/queries.json          (or a per-round bank)
2. Run                 archi evaluate --argilla -cd configs/     (sweeps all configs in one snapshot)
3. Email evaluators    https://archi.rc.fas.harvard.edu:3080/    (the Argilla URL)
4. After grading       archi grade --export -o grades.json
5. Analyze             scripts/benchmarking/analyze_grades.ipynb
```

Steps 1, 2, 4, 5 are run by the benchmark operator. Step 3 is the evaluator-facing surface — they grade in the Argilla UI, no CLI access needed.

### CLI flags

```bash
# Run with Argilla push
archi evaluate -n bench-round-N -cd configs/ -e ~/.archi/.env.benchmark --argilla

# Custom Argilla URL (default http://localhost:6900)
archi evaluate ... --argilla --argilla-server http://my-argilla:6900

# Pull grades back to JSON
archi grade --export -o grades.json

# Open the Argilla UI in your browser
archi grade --serve
```

`archi grade --export` reads the last-run dataset name from `~/.archi/.last-benchmark` if `--dataset` isn't specified.

### Judge/SUT split

The RAGAS judge LLM and the system under test (SUT) are decoupled. By default the same model judges itself; this is a known bias problem (a model rates its own style higher). Set `mode_settings.ragas_settings.evaluator_*` to break the symmetry — typically run local Qwen as the SUT and HUIT Bedrock Claude as the judge:

```yaml
services:
  benchmarking:
    # SUT
    provider: local
    model: qwen3:32b
    ollama_url: http://host.containers.internal:7870
    mode_settings:
      ragas_settings:
        # Independent judge — Anthropic Claude via Harvard HUIT's Bedrock proxy
        evaluator_provider: huit_bedrock
        evaluator_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0
```

The `huit_bedrock` provider is Harvard's Anthropic-compatible Bedrock proxy. Pinning Sonnet 4.5 (rather than the rolling-alias 4.6) makes scores reproducible across rounds. Requires `HUIT_API_KEY` in `~/.archi/.env.benchmark`.

### Argilla configuration

```yaml
services:
  benchmarking:
    argilla:
      # Number of distinct evaluators that must grade each record before it is
      # marked complete. Drives inter-rater reliability sample size.
      # Default 2; bump to 3 for high-stakes adoption decisions.
      min_submitted: 2
```

See `argilla/README.md` for the self-hosted Argilla setup, including secret generation, workspace bootstrap, and user account creation.

---

## Scientific-rigor conventions

These exist to make eval rounds trustworthy as an adoption signal, not just a vibes check.

### Pre-registration

Before each eval round, write a pre-reg using the template at `docs/eval/preregs/_template.md`. Capture: primary hypothesis, the metric that decides, the decision rule (incl. what would make us NOT adopt the change), and any planned secondary analyses. **Lock the pre-reg before running the eval** — committing it on the benchmarking branch is the time-stamp.

The pre-reg defends against running the eval, seeing the results, and then post-hoc choosing whichever metric makes the preferred config look best.

### Anchor questions

`examples/benchmarking/anchor_questions.json` holds 3-5 questions of three types that are run on **every** round:

- **Easy-retrieve:** specific FASRC fact like a partition name or a quota. Should always score high; if it regresses, the retrieval pipeline broke.
- **Reasoning:** a multi-step troubleshooting question that needs synthesis across multiple docs. Best signal for prompt/model changes.
- **Should-refuse:** an out-of-scope question (e.g. about a non-FASRC system). The right answer is "I don't know" or a referral, not a hallucination.

Anchors detect cross-round regressions and ground the comparison. They should NOT be in the main question bank — that's a separate per-round set.

### Annotation rubric and calibration

See `docs/eval/rubric.md` for the four-widget annotation rubric (winner / quality / failure-mode tags / notes), the binary-vs-Likert rationale, and the calibration-round protocol (group-grade the first 10 records, discuss, then go independent).

### Inter-rater reliability

The analysis notebook computes pairwise Cohen's kappa (per pair of graders), Fleiss' kappa (overall), and per-grader bias distribution. Aim for κ ≥ 0.4 ("moderate agreement") before treating round-N's winner as decisive.
