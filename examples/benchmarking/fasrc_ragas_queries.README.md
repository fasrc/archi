# FASRC RAGAS query bank

`fasrc_ragas_queries.json` is a RAGAS-formatted question bank for the archi
benchmark harness (`src/bin/service_benchmark.py`). The harness answers each
question with the live agent, then scores the run with RAGAS and pushes the
records to Argilla for human grading.

## File format

A single JSON **array** of question objects, one per question, in ragas 0.3.5's
modern schema (`user_input`/`reference`). This is the shape the benchmark loader
(`queries_path`) and the anchor file (`anchor_questions.json`) use. Banks authored
in the legacy `question`/`answer` schema are still accepted — the harness
normalizes them on read (`question`→`user_input`, `answer`→`reference`,
`contexts`→`retrieved_contexts`), so existing and externally-supplied files keep
loading.

```json
[
  {
    "user_input": "Which SLURM partition on Cannon should I submit GPU jobs to?",
    "reference": "Use the gpu partition (or gpu_test for short test jobs). Request GPUs with --gres=gpu:N.",
    "sources": ["https://docs.rc.fas.harvard.edu/kb/running-jobs/"],
    "source_match_field": ["url"],
    "notes": "optional authoring note; never scored"
  }
]
```

### Fields

| Field                | Type        | Required | Purpose |
|----------------------|-------------|----------|---------|
| `user_input`         | str         | always   | The query posed to the agent (ragas `user_input`). |
| `reference`          | str         | RAGAS mode¹ | Ground-truth answer (ragas `reference` — **not** `response`, which is the agent's run-time answer). |
| `sources`            | list[str]   | SOURCES mode | Reference source URLs the answer should be grounded in. |
| `source_match_field` | list[str]   | with `sources` | How each source is matched, e.g. `["url"]`. |
| `notes`              | str         | no       | Authoring notes (e.g. "confirm with operator"). Not scored, not shown to graders. |
| `status`             | str         | no       | Confirmation state: `draft` or `locked`. Absent ⇒ treated as `draft`. Consulted only by the maintenance tooling — **never** by scoring. |
| `source_hashes`      | dict        | on `locked` | Map of each `sources` URL → the content hash of that page's normalized extracted text, captured at lock time. The drift tripwire. Absent on `draft` rows and on source-less `should_refuse` rows. |

¹ `reference` is **not required at load** — an empty `reference` is a valid draft
row. Load validation requires only `user_input` (plus `sources` for SOURCES mode),
kept separate from metric eligibility. A draft row is simply skipped by the context
metrics (`context_precision`/`context_recall`, which need a ground truth) while
`answer_relevancy`/`faithfulness` still score it.

### Confirmation state (`draft` / `locked`)

`status` makes "is this reference trustworthy?" a machine-queryable field instead of
free text in `notes`. A row is authoritative ground truth for the maintenance tooling
**only** when `status` is exactly `locked`; anything else — absent, `draft`, or an
unexpected value — is a non-authoritative `draft`. Every row is currently backfilled to
`draft`; an operator promotes one to `locked` after confirming its answer against the
live source, at which point the tool records `source_hashes` (one content hash per
`sources` URL). A locked `should_refuse` row has empty `sources` and so carries no
`source_hashes`.

**This does not change benchmark behavior.** The harness scores every row's `reference`
regardless of `status` (an empty `reference` is still skipped by the context metrics as
above). Gating scoring on `locked` — only scoring confirmed references — is a separate,
deferred change.

`retrieved_contexts` is **not** authored here — the harness fills it from the
agent's retrieved `source_documents` at run time, then hands the full
`user_input`/`retrieved_contexts`/`response`/`reference` record to RAGAS
(`response` is the agent's answer; `reference` is this file's ground truth).

## How it's consumed

- **RAGAS** — `service_benchmark.py` builds a ragas `EvaluationDataset` with
  `user_input`, `retrieved_contexts` (retrieved at run time), `response` (the
  agent's answer), and `reference` (this file's ground-truth answer), then runs
  `answer_relevancy`, `faithfulness`, `context_precision`, `context_recall`. Each
  metric is scored over only the rows eligible for it (the context metrics skip
  empty-`reference` rows) and the run reports each metric's `n_scored / n_total`.
- **Argilla** — `src/utils/benchmark_argilla.py` pushes each record (question,
  agent answer, retrieved trace, RAGAS scores) to the self-hosted Argilla stack
  (`argilla/`) for team human grading.

## Wiring a config to this file

Point a benchmarking config's `queries_path` at this file:

```yaml
services:
  benchmarking:
    queries_path: examples/benchmarking/fasrc_ragas_queries.json
    modes:
      - "RAGAS"      # add "SOURCES" to also score against `sources`
```

## Authoring workflow

Paste questions (and reference answers when available) and they are appended here
as objects in the array. Keep the JSON valid (it's a plain list — no comments).
A record with no confirmed ground truth yet can carry a `notes` flag and an empty
`reference` (a draft row) until the operator locks it — it is still scored on the
answer metrics, just skipped by the context metrics.

## Seeded content (2026-06-28)

The bank is seeded with **21 questions** grounded in live `docs.rc.fas.harvard.edu`
KB pages fetched on 2026-06-28. Each carries an `anchor_type` (the same typology as
`anchor_questions.json`), so a benchmark run can be sliced by question difficulty:

| `anchor_type`    | Count | What it measures |
|------------------|-------|------------------|
| `easy_retrieve`  | 10    | A single fact is surfaced — a regression here means retrieval broke. |
| `reasoning`      | 8     | Multi-step / multi-fact synthesis — the best signal for prompt/model/rerank changes. |
| `should_refuse`  | 3     | Out-of-scope (other institutions' clusters, unverifiable figures) — correct behavior is to refer, not hallucinate. `sources` is intentionally empty. |

Source pages used: `running-jobs`, `cluster-storage`, `fairshare`, `quickstart-guide`,
`python`, `modules-intro`, `globus-file-transfer`.

**Two caveats before a scored run:**

1. **Answers are `DRAFT`.** Each `notes` field flags the answer as grounded-but-unlocked.
   Have an operator confirm before treating the RAGAS `reference` as authoritative — KB
   facts drift (e.g. GPU requests moved from `--gres=gpu:N` to `--gpus=1`, and lab dirs
   from `/n/holyscratch01` to `/n/holylabs`; `anchor_questions.json` still holds the stale
   forms).
2. **SOURCES mode needs URL reconciliation.** The `sources` URLs are the canonical KB
   page URLs as fetched. SOURCES mode matches them against the ingested document `url`
   metadata, which the sitemap-driven SPLIT ingest may store under a slightly different
   slug. RAGAS mode is unaffected (it scores `user_input`/`reference`/retrieved
   `retrieved_contexts` only) — start there, and verify URL matching before relying on
   SOURCES scores.
