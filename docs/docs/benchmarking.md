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

Provide questions, ground-truth answers, and correct sources in ragas 0.3.5's
modern JSON schema (`user_input`/`reference`). Banks authored in the legacy
`question`/`answer` schema are still accepted — the harness normalizes them on
read (`question`→`user_input`, `answer`→`reference`, `contexts`→`retrieved_contexts`).

```json
[
  {
    "user_input": "Does Jorian Benke work with the PPC?",
    "sources": [
      "https://ppc.mit.edu/blog/2025/07/14/welcome-our-first-ever-in-house-masters-student/",
      "CMSPROD-42"
    ],
    "reference": "Yes, Jorian works with the PPC and her topic is Lorentz invariance.",
    "source_match_field": ["url", "ticket_id"]
  }
]
```

| Field | Required | Description |
|-------|----------|-------------|
| `user_input` | Yes | The question to ask (ragas `user_input`) |
| `sources` | SOURCES mode | List of source identifiers (URLs, ticket IDs, etc.) |
| `reference` | No¹ | Ground-truth answer (ragas `reference`, used for RAGAS evaluation) |
| `source_match_field` | No | Metadata fields to match sources against (defaults to config value) |

¹ Only `user_input` is required at load (plus `sources` for SOURCES mode). An
empty `reference` is a valid draft row: it is skipped by the context metrics
(`context_precision`/`context_recall`) but still scored by `answer_relevancy` and
`faithfulness`.

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
| `ollama_url` | — | SUT base URL when `provider: local` (e.g. an Ollama server, or an OpenAI-compatible `/v1` endpoint such as a vLLM) |
| `provider_mode` | _auto_ | Local SUT client mode: `openai_compat` (ChatOpenAI) or `ollama` (ChatOllama). Auto-detected from `ollama_url` — a `/v1` endpoint → `openai_compat`, otherwise `ollama`. Set explicitly to override |
| `queries_path` | — | Path to the queries JSON file (ragas modern dialect `user_input`/`reference`; legacy `question`/`answer` banks are normalized on read) |
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
latency, and deployment image size. The config pair and grounded question bank
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
the retrieval treatment.

> **Why two runs, not one `-cd` directory.** Unlike the prompt sweep above, this
> A/B varies *ingestion and retrieval* config (chunking strategy + retriever),
> not the prompt. The `archi evaluate -cd` mode sweeps only `services.benchmarking`
> over a **single, once-ingested corpus** — its loader requires `global` (incl.
> `DATA_PATH`) to be identical across configs, and runtime retriever/chunking come
> from the once-seeded Postgres config. So each arm must be run as its **own**
> deploy + ingest + evaluate pass, and the two results compared offline.

### Run

Run each arm as a separate deployment, re-ingesting between them:

```bash
# Arm 1 — baseline: deploy + ingest with the baseline config, then evaluate
archi evaluate -n hr-ab-baseline -c examples/benchmarking/hierarchical_rerank_ab/baseline_character_hybrid.yaml --hostmode

# Arm 2 — treatment: redeploy + re-ingest with the treatment config, then evaluate
archi evaluate -n hr-ab-treatment -c examples/benchmarking/hierarchical_rerank_ab/treatment_hierarchical_rerank.yaml --hostmode
```

`--name`/`-n` is required by the `evaluate` CLI (it names the deployment). Each
pass ingests its own corpus at its own `DATA_PATH` and writes its own dump JSON;
compare the two runs' RAGAS aggregates offline to get the baseline-vs-treatment
delta.

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
2048/512, 4096/512) and run each as its own deploy+ingest+evaluate pass (same
two-run protocol — chunk size changes ingestion), comparing the aggregates
offline. The same pattern sweeps `retrievers.hybrid_retriever.bm25_weight`.

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

> **Before you report a result as an improvement, read
> [Interpreting Benchmark Results](interpreting_benchmark_results.md).** This page
> covers how to *run* a benchmark; that one covers how to tell whether the number
> that came out means anything. It defines a mandatory gate for any result used to
> justify shipping a change.

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

---

## Maintaining the golden set (`coverage` / `orphans` / `drift` / `report`)

The question bank falls out of step with the knowledge base in three directions: the KB grows
pages nobody wrote a question for, it removes pages some question still cites, and it rewrites
pages a confirmed answer was checked against. Three read-only subcommands find each:

```bash
# Which ingested pages does no bank row ground against?
python scripts/benchmarking/goldenset_maintenance.py coverage \
    --bank examples/benchmarking/fasrc_ragas_queries.json \
    --pg-dsn "postgresql://archi@localhost/archi-db"

# Which bank rows cite a page the live KB no longer publishes?
python scripts/benchmarking/goldenset_maintenance.py orphans \
    --bank examples/benchmarking/fasrc_ragas_queries.json \
    --sources config/lists/sources.list \
    --min-pages 150

# Which confirmed rows were grounded in a page that has since changed?
python scripts/benchmarking/goldenset_maintenance.py drift \
    --bank examples/benchmarking/fasrc_ragas_queries.json \
    --allowed-hosts docs.rc.fas.harvard.edu slurm.schedmd.com
```

`--min-pages` is the sitemap **completeness floor** and must match the deployment's
`data_manager.sources.links.sitemap.min_pages` (FASRC: 150). It is **required** whenever the
source list contains a `sitemap-` line, and the command refuses to run without it: the library
default is 1, under which a truncated sitemap expands "successfully" and every bank URL absent
from that partial response is reported as an orphan. `--max-pages` and `--allowed-hosts` take
the same values as the deployment config.

**All three are proposal-only.** They print work lists and leave the bank file byte-unchanged;
adding a question, locking a reference, re-baselining a drifted row, or pruning an orphan is a
separate step you take deliberately. Findings **exit zero** — a gap is work to do, not a broken
run — so a cron job only alarms on an operational failure: an unreadable bank, corpus, or source
list, a missing sitemap floor, or an **abstained** run (below).

### Why the three passes read different sources

They look inconsistent side by side, and the difference is deliberate — each pass asks a
different question, and the right oracle follows from the question:

| Pass | Reads | Because |
| --- | --- | --- |
| `coverage`, `--propose` | the **persisted corpus** | A golden question must be answerable from the text the retriever actually serves. |
| `orphans` | the **live source inventory** | The corpus never prunes, so a removed page still has a row in it. |
| `drift` | a **live re-fetch** | The corpus lags in-place edits, so it would agree with a stale reference and call it clean. |

The corpus is trustworthy for *which URLs were ingested* and for *what the index holds*, and for
nothing else. Ingestion is URL-keyed and skips the content write for a page it already has
(`persist_resource(..., overwrite=False)`), so a page edited in place keeps its old stored text
until someone forces a re-ingest. That single fact is why `drift` must re-fetch and why
`--propose` must not.

### `coverage` — pages with no question

Coverage is re-derived from the bank on **every** run. Drafting candidate questions for a page
does not mark it covered; only an applied bank row citing that URL does. So a page you
greenlit but never finished writing up keeps showing as a gap, which is the honest answer.

Gaps are grouped by **parent source** (the host for a web source, the repository for a git
one) and can be narrowed, so one large git source contributing thousands of per-file URLs
does not bury a handful of real KB gaps:

```bash
--source-type web                     # only web sources
--parent https://docs.rc.fas.harvard.edu
--path-glob 'https://docs.rc.fas.harvard.edu/kb/*'
```

Coverage reads only **retrievable** documents — not soft-deleted, and
`ingestion_status = 'embedded'`. A document still `pending`/`embedding`, or one that `failed`,
has no chunks for the retriever, so a golden question written against it would be unanswerable
and would score as a benchmark failure. A page stuck outside `embedded` is an *ingestion*
problem, not a golden-set gap.

Use `--corpus-json <file>` instead of `--pg-dsn` to run against a JSON dump of the
`documents` rows — useful offline, or to reproduce a report without database access. **Both
inputs apply the same retrievability filter**, so an offline run reproduces the live one rather
than inventing gaps the live report never shows. A dumped row that omits `ingestion_status`
entirely is *kept*: the column cannot be judged if it isn't there, and dropping such rows would
empty the report and read as "fully covered" — a silent false clean. Dump the column if you want
the offline run filtered.

### `coverage --propose` — draft questions for one greenlit page

A gap is a suggestion, not an order. Nothing drafts a question until you name a page — **one page
per invocation, deliberately**. The filters above narrow what you *review*; they never batch a
decision. Drafting for every page under a path glob is the auto-covering this design rejects: a
golden set's value is signal per question, not count, and a hundred machine-drafted rows on a
low-value source is debt someone has to read. Working through a large source is a loop over
single greenlights — which is what the conversational skill does on your behalf.

```bash
python scripts/benchmarking/goldenset_maintenance.py coverage \
    --bank examples/benchmarking/fasrc_ragas_queries.json \
    --pg-dsn "postgresql://archi@localhost/archi-db" \
    --propose https://docs.rc.fas.harvard.edu/kb/running-jobs \
    --model anthropic/claude-sonnet-5 \
    --data-path ~/.archi/archi-fasrc-dev/data \
    --count 3
```

Candidates are grounded in the **persisted document** — the converted text on disk at
`documents.file_path`, which is what got chunked, embedded, and is served at query time — and
**never in a live re-fetch**. That distinction is the whole point: ingestion is URL-keyed and
skips the content write for a page it already holds, so the live page can be *ahead* of the
index. Grounding in live text would author a question about a fact the retriever cannot serve —
the same unanswerable-question trap the retrievability filter closes, one layer down. `--data-path`
is the deployment's data root, which `file_path` resolves against; `--propose` refuses without it
rather than falling back to a fetch.

The drafts print as a JSON array ready to paste into the bank — **the run writes nothing**.
Applying a candidate is your edit, and reviewing it before that edit is the point.

Three properties are imposed by the tool, not accepted from the model:

- **`status` is always `draft`.** There is no code path that emits a locked candidate, so a
  confused or hostile reply cannot smuggle one in. Locking stays a human act on an applied row.
- **`sources` is always the page you greenlit.** Whatever URL the model cites, the row cites
  the page you named — grounding is the tool's guarantee.
- **`anchor_type` must be `easy_retrieve` or `reasoning`.** Anything else is rejected with a
  reason printed to stderr rather than silently relabeled. `should_refuse` is rejected too: those
  rows carry **no** `sources` by design, so one "grounded in" a page is a contradiction.

`--propose` drafts for a **gap**, and refuses anything else:

- a URL **already covered** by a bank row — a second question on a covered page adds count, not
  signal, and reads as valid once pasted in;
- a URL that is a **slug near-miss** for a covered one — the tool has said it cannot tell whether
  the page is covered, so drafting on top of that unknown is how a duplicate gets authored under
  the moved slug. Reconcile first;
- a URL not in the **retrievable** corpus, one whose row carries no `file_path`, and one whose
  persisted document is missing or empty.

Every one of those refusals exits non-zero instead of degrading to a live fetch.

It also refuses any `file_path` that resolves **outside `--data-path`** — absolute, `..`-relative,
or via a symlink — before reading a byte. `file_path` comes from the catalog or from a
`--corpus-json` dump you were handed, and its contents are sent to an external model provider, so
an unchecked path is a file-disclosure channel off the machine. The same check catches the dull
case: a stale path that would silently ground a question in an unrelated file. Absolute paths
*inside* the data root are still fine, matching what the catalog itself stores.

If every candidate is rejected the run also exits **non-zero** — a propose run that produced
nothing is a failed run, not a finding, which is the opposite of how gaps and orphans exit.

### The decision ledger — declines only

Some pages will never earn a question. Record that once instead of re-reading it every run:

```bash
# dismiss a page
python scripts/benchmarking/goldenset_maintenance.py coverage \
    --bank examples/benchmarking/fasrc_ragas_queries.json \
    --pg-dsn "postgresql://archi@localhost/archi-db" \
    --decline https://docs.rc.fas.harvard.edu/kb/contact \
    --reason "contact page — nothing to ask" \
    --ledger .ralph/log/goldenset-declines.json

# change your mind — the supported reversal
python scripts/benchmarking/goldenset_maintenance.py coverage \
    --bank examples/benchmarking/fasrc_ragas_queries.json \
    --undecline https://docs.rc.fas.harvard.edu/kb/contact \
    --ledger .ralph/log/goldenset-declines.json

# later runs suppress it
python scripts/benchmarking/goldenset_maintenance.py coverage \
    --bank examples/benchmarking/fasrc_ragas_queries.json \
    --pg-dsn "postgresql://archi@localhost/archi-db" \
    --ledger .ralph/log/goldenset-declines.json
```

`--decline` obeys the **same gap rule as `--propose`**: it needs a corpus, and it refuses a page
that is already covered, is a slug near-miss, or is not in the retrievable corpus. The two flags
are the two dispositions of one decision — "this gap earns a question" and "it does not" — and
neither is a claim you are in a position to make about a page you never reviewed as a gap.
Recording a typo'd or covered URL would sit in the ledger and silently suppress that page if it
ever *became* a gap.

`--propose` **refuses** a page whose decline still stands, pointing you at `--undecline`.
Overriding the decline for just that one run would be worse than useless: the
drafts start out unapplied, so nothing covers the page, and the stale entry would keep hiding it
from every later report — a permanent false-clean with no recovery but hand-editing the file.

The ledger records **declines only** — never "drafted" or "covered". That asymmetry is
deliberate: covered-ness is re-derived from the bank every run, so a page whose candidates you
drafted and then abandoned stays a visible gap until a row actually lands. A ledger that also
suppressed drafted URLs would make that abandoned page read as clean forever.

Suppressed pages are **counted and listed**, not silently filtered — a report that hides pages
without saying so reads as clean when it isn't. Declining is idempotent (declining twice keeps
the first entry and its reason), and undeclining a page that was never declined is a no-op that
says so.

The ledger is the **only** file this tool ever writes, and unlike coverage a decline cannot be
re-derived from anything, so the update is protected twice over:

- **Atomic and durable** — written to a temp file in the same directory, fsynced, `os.replace`d
  over the target, and then the parent directory is fsynced too. A truncate-then-write would lose
  every decline the file held if the write were interrupted; syncing only the file leaves the
  *rename* unpersisted, so a host crash right after an apparently successful run could resurrect
  every dismissed page. Errors are reported relative to that commit point: a failure **before**
  the replace says the ledger could not be written and is safe to retry, while a failure to sync
  the directory **after** it says the change *was* made but is unconfirmed — do not redo it.
- **Locked** — read, merge and replace run under an exclusive lock on a `<ledger>.lock` sidecar,
  so two operators (or two agent sessions) declining at once cannot each read the same state and
  have the second replacement erase the first. The lock is a sidecar rather than the ledger itself
  because `os.replace` swaps the ledger's inode, and a lock on the old inode would not block the
  next writer. Where the platform cannot provide that lock (`fcntl` is POSIX-only), `--decline`
  and `--undecline` **refuse** rather than proceed with a warning — the lost update would happen
  either way and you would have no way to notice. The read-only passes are unaffected.

A missing ledger reads as "nothing declined yet". Anything **malformed fails the run** — bad JSON,
not an array, or a single entry without a usable `url`. That includes one broken entry among good
ones: a dropped decline fails in the visible direction (the page just reappears as a gap) but it
does so silently on an otherwise green run, and a decline is the one record here that cannot be
re-derived from the bank. Declining into a ledger the tool cannot fully read is refused for the
same reason.

### `orphans` — questions whose page is gone

Orphan detection compares the bank against a **freshly expanded live source inventory**, not
against the ingested corpus. This matters: the ingest never prunes. A page deleted upstream
still has a corpus row forever, so "it's still in the corpus" is no evidence the page exists,
and "it's missing from the corpus" is no evidence it was removed. `sitemap-` lines in the
source list are expanded live (the same expansion the ingest uses); every other line is a
hand-listed page. A `URL,depth` suffix is stripped exactly as the ingest strips it, so the
oracle and the corpus agree on what a source line means — otherwise a line like `…/kb/a,2`
would inventory a URL the corpus never stored and its bank row would read as removed.

The full set of typed source prefixes is mirrored from the ingest's router. `sitemap-` expands;
`sso-` is stripped (it only tells the ingest to authenticate, so the line is still one page).
`git-`, `elog-` and `indico-` — plus an unprefixed ELOG/Indico URL the ingest would auto-detect
— **fan out** into many sub-documents (a git source ingests one document *per file*) that this
read-only tool cannot enumerate without cloning or crawling. Those sources contribute no URLs
and are reported as **not enumerable**, which keeps their hosts out of scope so their bank rows
are never proposed for prune. Without that, `git-https://github.com/org/repo` would parse as
host `github.com`, put that host in scope, and turn every bank row citing the repo into a false
orphan.

Three guards keep the pass from crying wolf. Each shows up as its own bucket in the output:

- **Abstention.** Sitemap expansion *fails open* — a sitemap that will not fetch or parse
  contributes zero URLs with only a warning. If any source document failed, the expansion fell
  below its configured floor (`--min-pages`), or the inventory came back empty, the run reports
  `ABSTAINED` on stderr and flags nothing. A partial inventory would make every unlisted page
  look deleted. Abstention **exits non-zero**: no analysis happened, so it is an operational
  failure, not a clean bill of health — a cron that treated it as success would hide a broken
  inventory indefinitely.
- **Out of scope.** The inventory can only speak for the hosts it actually contains. A row
  citing an external authority the KB never ingested — the upstream Slurm docs, say — was
  never in the KB to be removed, so it is listed as out of scope rather than judged.
- **Needs reconciliation.** A URL that matches only by *slug near-miss* — **same host**, plus the
  same final page name after ignoring the path prefix, an `.html` extension, and a
  WordPress-style `-2` collision suffix — is neither an orphan nor a gap. A page that merely moved
  (`/kb/x` → `/docs/x`) or picked up an alias lands here for a human to confirm. A genuine rename
  (`running-jobs` → `submitting-jobs`) deliberately does *not* match, so it stays visible as
  real work.

    The **same-host** requirement matters: the bank cites external authorities (the upstream
    Slurm docs). Without it, `slurm.schedmd.com/mpi` would "reconcile" a KB page `…/kb/mpi` and
    hide a genuine coverage gap behind a bogus pairing — `coverage` has no scope guard of its
    own, unlike `orphans`. When several same-host pages share a leaf slug, **every** candidate is
    listed rather than the pairing being narrowed to one: that ambiguity is exactly what a human
    is being asked to settle.

Nothing is ever deleted. An orphan is reported with its row index and the removed URL so you
can decide whether to re-ground the question, rewrite it, or drop it.

### `drift` — confirmed answers whose page changed

A `locked` row is one a human read and vouched for against a specific page. `drift` asks whether
that page still says what it said. It works in two stages, cheap first:

1. **Hash tripwire.** Re-fetch every `sources` URL of every locked row, extract the text, and
   compare a hash of it against the row's stored `source_hashes` entry. Pure fetch-and-compare —
   no model, no state file, no timestamps.
2. **LLM diff.** *Only* for a source whose hash moved, ask a model whether the recorded
   `reference` still holds against the page as it reads now. The verdict is advisory.

```bash
# tripwire only — cheap enough to run on a schedule
python scripts/benchmarking/goldenset_maintenance.py drift \
    --bank <bank.json> --allowed-hosts docs.rc.fas.harvard.edu

# escalate every moved hash to a model for triage
python scripts/benchmarking/goldenset_maintenance.py drift \
    --bank <bank.json> --allowed-hosts docs.rc.fas.harvard.edu \
    --model anthropic/claude-sonnet-5
```

`--model` is optional. Without it you get the tripwire alone, which is already a real finding:
"this page changed" is worth knowing even before anyone decides whether the answer broke.

#### What gets checked

Only **`locked`** rows. A `draft` row is unconfirmed by definition — no one ever vouched for its
answer against a page — so there is nothing for a hash to be a baseline *of*, and its sources are
not even fetched. A locked row with no `sources` (the `should_refuse` shape) has nothing to check
either; locking one must never require a grounding hash.

A row is flagged when **any** of its sources moved, and the report names which one. A row
grounded in three pages where one was rewritten is exactly as stale as a row grounded in a single
rewritten page.

#### Hashes are taken over extracted text, not markup

The hash is computed over the page's text *after* the ingest's own HTML→Markdown extraction
(`processing.html_to_markdown`, the same function the scraper persists through), then normalized
for line endings, Unicode composition form, runs of spaces, and runs of blank lines. Wording,
case and punctuation are left exactly as written — those are the content.

This is what keeps a theme change, a reflowed paragraph, or a re-indented list from reading as a
fact change. It is not perfect: on a **non-KB** page the extraction keeps site chrome, so a
changed nav or footer can still move the hash. That is the cost of the direction this errs in —
a false positive is one page for a human to glance at, while a false negative is a wrong answer
that stays in the benchmark scoring as correct. The LLM diff exists to make those glances cheap.

#### What `drift` will and will not fetch

`drift` is the one pass that turns a `sources` value into an outbound request, and `sources` is
data read out of a file. So `--allowed-hosts` is **required** — it is the list of hosts drift is
authorized to contact, and there is no allow-everything default:

```bash
python scripts/benchmarking/goldenset_maintenance.py drift \
    --bank <bank.json> --allowed-hosts docs.rc.fas.harvard.edu slurm.schedmd.com
```

The bank legitimately grounds some questions in external authorities (the upstream Slurm docs),
so the list is usually more than one host — but naming them is the operator's call, not the bank
file's. Every URL is then checked against the ingest's own trust filter (`is_url_allowed`)
*before* it is dialled. Refused unconditionally, allowlisted or not:

- any scheme that is not `https` (see **TLS** below — the ingest permits `http`; drift does not)
- a loopback, private, or link-local address (`127.0.0.1`, `10.0.0.5`, `169.254.169.254`)
- an obfuscated numeric host that resolvers still map to those (`2130706433`, `0177.0.0.1`)
- a malformed or zero port

A refused URL is **never contacted and never shown to a model** — it is reported in its own
bucket, not counted as clean. This matters because reusing the ingest's *fetcher* does not
inherit the ingest's *target policy*: `fetch_sitemap_text` enforces transport limits (no
cross-host redirects, a bounded body, a timeout), while the trust filter lives in
`expand_sitemaps`. Drift applies it itself.

**TLS is verified, and the connection may never leave HTTPS.** The ingest's fetcher defaults to
`verify=False`; drift overrides that. A network attacker who could substitute page content would
otherwise be able to manufacture drift findings, steer the advisory verdict, and put text of their
choosing into a prompt sent to the model provider. There is deliberately no flag to turn
verification off — such a flag ends up in the cron line. A deployment with a private CA sets
`REQUESTS_CA_BUNDLE`.

Verification only protects a hop that is still TLS, so two plaintext routes are closed alongside
it. A `sources` URL written as `http://` is refused by the policy above and named in the report,
even on an allowlisted host — `sources` is repo-committed data, so writing `https` is a
one-character edit. And an `https://` page that redirects to `http://` **on its own host** passes
the fetcher's host check, which compares hosts and not schemes; drift refuses that downgrade at
the transport. Ingestion is unaffected — it still follows such redirects, because it forwards
nothing to a model provider.

**Not covered:** DNS-rebinding-resistant connection pinning. This layer sees only a *hostname* —
nothing resolves DNS or inspects the address actually connected to — so a name that passes the
filter but resolves to an internal address is not caught. That is precisely why the allowlist is
mandatory: it narrows the exposure from "any name the bank happens to contain" to "a host the
operator vouched for", which is the part a hostname check can enforce.

The real fix is docketed against `fetch_sitemap_text` itself
([#143](https://github.com/fasrc/archi/issues/143), §Deferred hardening v2/H1) rather than
reimplemented here, because the same gap applies to `expand_sitemaps` — which fetches URLs it
read out of a **remote** document, a strictly less trusted source than a repo-committed bank —
and a second, divergent fetch path is the failure mode this module is built to avoid.

#### Seeing the evidence

`--show-text` prints the fetched page text behind each finding, delimited per URL:

```bash
python scripts/benchmarking/goldenset_maintenance.py drift \
    --bank <bank.json> --allowed-hosts docs.rc.fas.harvard.edu \
    --model anthropic/claude-sonnet-5 --show-text
```

It is opt-in because the report is a work list and a page body per changed row would bury it —
but with `--model` you would otherwise be reading a one-sentence verdict about text you cannot
see, which is not a review. Note this is the **new** page, not a diff: the tool stores only a
hash of the old one, which is exactly what lets it keep no state between runs.

A very long page is cut, and the cut is marked with `[... page truncated ...]`. The hash still
covers the whole page — only the text kept for review is capped — but the model sees the same
truncated copy you do, so a verdict on a long page is a verdict on its opening section.

#### Stored hashes carry their algorithm

A `source_hashes` value looks like `sha256:9f86d081…`, not a bare hex string. The label is what
lets the tool tell "this baseline was computed a different way" from "this page changed" — a bare
digest cannot say how it was made, and guessing would either flag the whole bank at once or,
worse, agree by coincidence. A stored value the tool does not recognize is reported as
**incomparable** and is never counted as clean.

#### Everything that isn't `unchanged` or `changed` is reported

A page the tool could not check must never read as a page that is fine, so each unjudgeable state
gets its own bucket in the report:

- **No baseline recorded** — the row is locked but has no `source_hashes` entry for that URL.
  Nothing to compare against, so it is neither drifted nor clean.
- **Incomparable baseline** — the stored value is not a well-formed `sha256:` digest (see above):
  an unrecognized algorithm label, or a hand-edited value that kept the label but lost the digest
  (`sha256:9f86d0818`). Either way there is nothing a fresh hash could equal, so it is reported as
  unchecked rather than as a change.
- **Unreachable** — the fetch failed. Explicitly *not* evidence the page is unchanged.
- **Refused** — the URL was rejected by the fetch policy above, so it was never contacted.
- **Stale baselines** — a `source_hashes` entry for a URL the row no longer cites, left behind
  when someone edited `sources`. Harmless on its own, but it means a recorded confirmation
  refers to a page that is no longer part of that question. Reported even when `sources` was
  emptied *completely*, which is the largest version of the same edit — that row is still counted
  as skipped, because nothing was fetched or compared, but its orphaned baselines are named.

#### Recording a baseline

The tool never writes the bank, so `--print-hashes` is how a baseline gets recorded: it prints a
paste-ready block per row.

```bash
python scripts/benchmarking/goldenset_maintenance.py drift \
    --bank <bank.json> --allowed-hosts docs.rc.fas.harvard.edu --print-hashes
```

```json
{
  "source_hashes": {
    "https://docs.rc.fas.harvard.edu/kb/gpu-computing": "sha256:9f86d081884c7d65…"
  }
}
```

**Declare the lock first, then take the baseline.** Blocks are produced for `locked` rows only, so
locking a confirmed candidate is two edits to the same row:

1. set `status: locked` on the row (this is the human act — you are vouching for the reference
   against that page),
2. run the command above and paste the block it prints for that row.

Between the two the row is locked with no baseline, which is a state the report already names
(*no baseline recorded*) rather than a broken one. The order is deliberate: a baseline is a record
of a confirmation, so producing one for a `draft` row would manufacture the evidence of a
confirmation that has not happened. A run that prints no blocks says so and names this reason.

Do the same after reviewing a drifted row and deciding the answer still holds — that re-baselines
it so the next run is quiet again. Both are human acts, on purpose: a tool that re-baselined a
drifted row by itself would erase the finding before anyone read it.

Pasting replaces the row's whole map, so each block is **complete for its row**: a source that
could not be read this run carries its existing baseline forward rather than vanishing. Where a
source has neither a fresh nor a stored hash there is nothing to carry, and the block is labelled
`INCOMPLETE` — pasting it as-is would drop that source. Baselines for URLs the row no longer
cites are deliberately not carried forward; they are reported separately as stale.

#### Abstention

If **no source was read at all** — every one unreachable or refused — the run reports `ABSTAINED`
on stderr and **exits non-zero**. Nothing was read, so "no drift" would be a clean bill of health
for a check that never happened. Refusals count here too: a mistyped `--allowed-hosts` rejects
every source, and that must not exit zero.

A *single* unreachable page does not abstain — unlike the orphan pass, where one missing sitemap
makes unrelated rows look deleted. A failure here is local: it affects only the rows citing that
URL, and those rows are individually reported as unchecked. Each URL is fetched at most once per
run, failures included, so a bank citing one page from twenty rows makes one request rather than
twenty.

#### Advisory, always

`drift` never edits `reference`, `status`, or `source_hashes`, and a model verdict never removes
a row from the report. The hash mismatch is the fact; the verdict only tells you how urgently to
look. A `holds` reply still leaves the row listed — putting a model in charge of whether a real
change gets reviewed is exactly the failure the tripwire exists to prevent.

### `report` — all three passes, for a cron

`report` runs `coverage`, `orphans` and `drift` in one read-only pass and prints the three work
lists together. It is the shape meant for an unattended nightly job:

```bash
python scripts/benchmarking/goldenset_maintenance.py report \
    --bank examples/benchmarking/fasrc_ragas_queries.json \
    --pg-dsn "postgresql://archi@localhost/archi-db" \
    --sources config/lists/sources.list \
    --allowed-hosts docs.rc.fas.harvard.edu slurm.schedmd.com \
    --min-pages 150 \
    --ledger .ralph/goldenset-declines.json
```

`--allowed-hosts` serves both passes that need one — the hosts `drift` may contact and the extra
hosts the sitemap may emit — because in practice they are the same list: hosts the operator
vouched for.

#### Findings exit zero; only a broken pass exits non-zero

A gap, an orphan and a drifted row are work to do, not a failed run. A cron that exits non-zero
whenever it finds something teaches its reader to ignore the alert, which is worse than having no
alert. Non-zero is reserved for a pass that **could not run** — an unreadable corpus or bank, a
missing source list, or a live inventory too incomplete to judge orphans against.

#### One broken pass does not hide the other two

The three passes read three independent things: the corpus catalog, the live source inventory, and
the source pages themselves. If the corpus is unreachable, orphans and drift are still perfectly
answerable, so `report` runs all three regardless and collects the failures. It prints them
together at the end, under `passes that could not run`, and exits non-zero — so the exit code and
the output agree about what happened. Stopping at the first failure would throw away two working
checks in order to report one broken one.

#### No model is called unless you name one

`--model` is accepted but omitted by default, so the nightly run is the cheap hash tripwire alone.
That is still a real finding: the mismatch is the fact, and the LLM diff only triages how urgently
to look. Paying a provider for every drifted row, every night, unattended, should be something an
operator opts into rather than inherits.

#### Read-only, like every other pass

`report` writes nothing — not the bank, not the corpus, not the source list. The decision ledger
is passed so that pages already declined stay suppressed from the nightly gap list; `report` reads
it and never appends to it, because declining a page is an interactive decision.

#### Running it nightly

`scripts/benchmarking/goldenset_report_cron.sh` wraps the command above for cron. It is
configured by environment variables rather than flags, so the crontab line stays one short entry
and the settings live somewhere a human can read them:

| Variable | Meaning |
| --- | --- |
| `GOLDENSET_BANK` | Bank JSON (defaults to `examples/benchmarking/fasrc_ragas_queries.json`) |
| `GOLDENSET_PG_DSN` / `GOLDENSET_CORPUS_JSON` | The corpus — exactly one of the two |
| `GOLDENSET_SOURCES` | Source list the KB ingests from (**required**) |
| `GOLDENSET_ALLOWED_HOSTS` | Space-separated hosts to contact (**required**) |
| `GOLDENSET_MIN_PAGES` / `GOLDENSET_MAX_PAGES` | Sitemap floor/cap — match the deployment |
| `GOLDENSET_LEDGER` | Decision ledger, so declined pages stay suppressed |
| `GOLDENSET_MODEL` | Optional advisory drift diff; unset means no provider calls |
| `GOLDENSET_LOG_DIR` | Where to append (default `~/.ralph/log`) |

**Install** — one crontab line on the dev server:

```bash
crontab -e
```

```cron
# nightly RAGAS golden-set maintenance report (read-only)
15 6 * * * GOLDENSET_PG_DSN="postgresql://archi@localhost/archi-db" \
  GOLDENSET_SOURCES="$HOME/archi/config/lists/sources.list" \
  GOLDENSET_ALLOWED_HOSTS="docs.rc.fas.harvard.edu slurm.schedmd.com" \
  GOLDENSET_MIN_PAGES=150 \
  GOLDENSET_LEDGER="$HOME/.ralph/goldenset-declines.json" \
  $HOME/archi/scripts/benchmarking/goldenset_report_cron.sh
```

**Rollback** — delete that line (`crontab -e`, remove, save). The wrapper holds no state, installs
no unit, and writes only its log, so removing the line is the whole rollback. Deleting
`~/.ralph/log/goldenset-report.log` is optional.

**Verify without waiting for the timer** by running the wrapper by hand with the same variables;
it prints to the terminal as well as the log.

Three properties make it safe unattended, each pinned by
`scripts/benchmarking/test_goldenset_report_cron.sh`:

- **Only a broken run mails you.** Findings exit zero; a pass that could not run exits 1, and a
  misconfigured wrapper exits 2 *before* invoking anything — a half-run report reads exactly like
  a clean one.
- **The log is appended, never truncated.** The history is the point: a page edited a little each
  month only becomes visible across runs.
- **No provider calls unless `GOLDENSET_MODEL` is set.**
