# Benchmarking scripts

Helpers to run and analyze the `archi evaluate` benchmark, plus read-only
maintenance for the RAGAS golden-set bank.

## Golden-set maintenance

- **`goldenset_maintenance.py`** — read-only detection over the golden-set bank.
  Subcommands: `coverage` (ingested pages no bank row grounds on), `orphans`
  (rows whose grounding page left the KB), `drift` (locked rows whose page
  changed), and `report` (all three in one unattended pass). It writes nothing
  but the declines-only decision ledger; every finding is a proposal for a human.
- **`goldenset_report_cron.sh`** — wraps `report` for a nightly cron, configured
  from an environment file. Mails a one-line digest only when there is work to do.
- **`test_goldenset_report_cron.sh`** — hermetic self-test for the wrapper, also
  run by `scripts/gate.sh`.

Full reference — every flag, the `--summary-json` schema, and the cron install —
lives in [`docs/docs/benchmarking.md`](../../docs/docs/benchmarking.md) under
**Maintaining the golden set**.

## Chunking measurement

- **`measure_chunk_overlap.py`** — measures the overlap the ingest's chunking
  *actually* carries across chunk boundaries, for a sweep of `chunk_overlap`
  values. `chunk_overlap` is a budget, not a guarantee: the splitter copies back
  only whole sentences that fit, so a budget below one sentence often carries
  nothing. Runs each loader document through the same two-level parent/child
  `HierarchicalNodeParser` the `sentence` strategy uses (one overlap budget at
  both levels, the loader's metadata replayed because the splitter subtracts it
  from every budget) and reads the carried text from the splitter's own
  character offsets, so a block a page happens to repeat is never mistaken for
  overlap. Reports empty boundaries, tokens carried, and the index inflation
  each setting costs; overlap 0 stays in the sweep as the control row, and at
  the ingest's own overlap the run reports how many of the stored children it
  reproduced. Needs no deployment — just the project env, a dump made with
  **`dump_chunk_overlap_corpus.sql`** (one JSON record per loader document,
  live parents of the target collection only), and, for exact text, a copy of
  the data manager's data directory passed as `--data-root`. Supports the #396
  feature matrix and the `chunking.chunk_overlap` key (#403).

## Feature-matrix runbook wrappers (`feature_matrix/`)

One thin wrapper per step of the #396 campaign protocol
([`docs/docs/proposals/feature-matrix-campaign-2026.md`](../../docs/docs/proposals/feature-matrix-campaign-2026.md),
§5); the arm configs live in archi-config under `config/benchmarking/feature_matrix/`.

- **`run_arm.sh <arm> <arm.yaml>`** — `archi evaluate -n fm-<arm> … --hostmode` (deploy,
  ingest, run). **`run_arm.sh <arm> --rerun`** re-runs only the benchmark container on the
  existing stack after proving the corpus fingerprint still equals the recorded pin.
- **`reseed_arm.sh <arm> <arm.yaml> [--stack fm-00]`** — switches a running stack to a
  retrieval-side arm without re-ingesting: copies the arm's `hierarchical_rerank` keys into
  the rendered config, re-runs `config-seed`, starts the benchmark container. Refuses an arm
  whose change is ingest-side (chunking, processing, stemming) — a re-seed cannot re-chunk
  what is already stored.
- **`qa_arm.sh <arm> <arm.yaml> [--stack …]`** — `archi eval qa` against the same stack.
  Refuses unless the stack's rendered config agrees with the arm YAML on every factor key,
  overwrites the rendered config's `chat_app` SUT fields from `services.benchmarking` (an
  evaluate stack renders the template defaults there), uses the campaign judge profile,
  and records the rendered config's sha256 and the corpus fingerprint in the ledger.
- **`archive_run.sh <arm> <run> <arm.yaml> [--wait]`** — records a finished run in
  `bench_out/feature_matrix/ledger.json`: fingerprint, digests, ingest seconds, live
  document and chunk counts, scored counts recomputed from finite values. Refuses an
  artifact whose recorded running configuration is not the arm's, one already in the
  ledger or older than the stack's latest `ragas-start`, a run whose
  `divergence_from_selected_file` is non-empty, and a later run whose fingerprint
  drifted; writes the corpus pin on run 1. Every wrapper refuses an arm label that does
  not match the YAML's own `name`. The live fingerprint is the harness's own routine
  (`CORPUS_STATE_QUERY` + `corpus_fingerprint`), run inside the data-manager container.
- **`test_feature_matrix_wrappers.sh`** — hermetic 20-check self-test (stubbed
  `docker`/`archi`, temp stack), run by `scripts/gate.sh`.

## Analysis and run helpers

The remaining scripts (notebooks, prompt-sweep generation, the Argilla push/reset
helpers, and `rebuild_benchmark.sh`) run and analyze the output of
`archi evaluate`.
