# FASRC RAGAS query bank

`fasrc_ragas_queries.json` is a RAGAS-formatted question bank for the archi
benchmark harness (`src/bin/service_benchmark.py`). The harness answers each
question with the live agent, then scores the run with RAGAS and pushes the
records to Argilla for human grading.

## File format

A single JSON **array** of question objects. One object per question. This is
the exact shape the benchmark loader (`queries_path`) and the anchor file
(`anchor_questions.json`) already use.

```json
[
  {
    "question": "Which SLURM partition on Cannon should I submit GPU jobs to?",
    "answer": "Use the gpu partition (or gpu_test for short test jobs). Request GPUs with --gres=gpu:N.",
    "sources": ["https://docs.rc.fas.harvard.edu/kb/running-jobs/"],
    "source_match_field": ["url"],
    "notes": "optional authoring note; never scored"
  }
]
```

### Fields

| Field                | Type        | Required | Purpose |
|----------------------|-------------|----------|---------|
| `question`           | str         | always   | The query posed to the agent. |
| `answer`             | str         | RAGAS mode | Reference / ground-truth answer. Becomes RAGAS `ground_truth`. |
| `sources`            | list[str]   | SOURCES mode | Reference source URLs the answer should be grounded in. |
| `source_match_field` | list[str]   | with `sources` | How each source is matched, e.g. `["url"]`. |
| `notes`              | str         | no       | Authoring notes (e.g. "confirm with operator"). Not scored, not shown to graders. |

`contexts` is **not** authored here — the harness fills it from the agent's
retrieved `source_documents` at run time, then hands the full
`question`/`answer`/`contexts`/`ground_truth` record to RAGAS.

## How it's consumed

- **RAGAS** — `service_benchmark.py` builds `Dataset.from_list([...])` with
  `question`, `contexts` (retrieved at run time), `answer` (the agent's answer),
  and `ground_truth` (this file's `answer`), then runs `answer_relevancy`,
  `faithfulness`, `context_precision`, `context_recall`.
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

Paste questions (and answers when available) and they are appended here as
objects in the array. Keep the JSON valid (it's a plain list — no comments).
Records with no confirmed `answer` yet can carry a `notes` flag and an empty or
placeholder `answer` until the operator locks it.

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
   Have an operator confirm before treating RAGAS `ground_truth` as authoritative — KB
   facts drift (e.g. GPU requests moved from `--gres=gpu:N` to `--gpus=1`, and lab dirs
   from `/n/holyscratch01` to `/n/holylabs`; `anchor_questions.json` still holds the stale
   forms).
2. **SOURCES mode needs URL reconciliation.** The `sources` URLs are the canonical KB
   page URLs as fetched. SOURCES mode matches them against the ingested document `url`
   metadata, which the sitemap-driven SPLIT ingest may store under a slightly different
   slug. RAGAS mode is unaffected (it scores `question`/`answer`/retrieved `contexts`
   only) — start there, and verify URL matching before relying on SOURCES scores.
