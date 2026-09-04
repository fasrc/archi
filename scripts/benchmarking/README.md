# Benchmarking scripts

Helpers to run and analyze the `archi evaluate` benchmark, read-only maintenance
for the RAGAS golden-set bank, and one converter that hands the same bank to
`archi eval qa`.

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

## QA dataset conversion

- **`ragas_bank_to_qa_dataset.py`** — converts the golden-set bank (plus the
  anchor questions) into a `qa-dataset-v2` file that `archi eval qa --dataset`
  accepts, so the gold-atoms QA evaluator and the RAGAS harness can score the
  *same* questions on the same stack and be read side by side. The QA dataset
  loader refuses RAGAS-dialect rows outright and only the browser import path
  maps them; this script is the command-line door to that same adapter, so the
  dialect mapping, the content-derived `qa-<sha256[:20]>` ids and the
  alias/duplicate refusals come from the library rather than a second copy of the
  rules. The question set is the harness's own — bank rows plus the anchor file,
  deduped on exact `user_input` with the bank row winning (105 + 5 − 1 = 109 on
  the FASRC bank) — which is what makes the ids recomputable from a RAGAS
  artifact's `question` + `reference_answer`, and the two runs comparable
  question for question — recompute the id from newline-normalized text, since
  the derivation folds CRLF to LF and the RAGAS artifact stores the fields
  verbatim. A bank row that carries its own `id` keeps it, so that item is
  matched by question text rather than by a recomputed id; the run report counts
  those rows (`explicit_ids`, 0 on the FASRC bank). Two items whose question and
  reference text are both identical — which a derived id refuses and an authored
  `id` allows — can only be told apart by run order: item order is preserved, so
  the Nth item is the artifact's `question_N` when `--status` dropped nothing and
  the anchors match. The report counts those as `text_duplicate_items` (0 on the
  FASRC bank). The script does not read the
  deployment configuration, so pass `--no-anchors` or `--anchors <path>` to
  mirror `services.benchmarking.anchors` when the run disables or relocates
  them. The bank is
  read with the import path's strict parser, so a repeated object key or a
  number binary floats cannot hold is refused rather than silently collapsed or
  rounded. `--no-anchors` converts the bank alone, `--status
  draft|locked` filters by confirmation state (repeatable), and `--json` prints
  the counts, carried fields and output sha256 as a machine report. Refusals are
  loud and named: a row spelling one concept twice (`user_input` *and*
  `question`), duplicate rows, a row with no `reference`, or a file that is
  already a QA dataset container (an object with a `schema_version`) exit 2
  instead of converting, and an `--out` that
  resolves to the bank or the anchor file exits 1 rather than replacing the
  source. Nothing is published until the written bytes have been read back as a
  dataset, so a refusal leaves any earlier output untouched. Supports the #396
  feature matrix.

  ```bash
  python scripts/benchmarking/ragas_bank_to_qa_dataset.py \
      config/benchmarking/fasrc_ragas_queries.json --out fasrc.qa-v2.json
  archi eval qa --dataset fasrc.qa-v2.json --agent-config <agent.yaml> --output-dir <run>
  ```

## Feature-matrix runbook wrappers (`feature_matrix/`)

One thin wrapper per step of the #396 campaign protocol
([`docs/docs/proposals/feature-matrix-campaign-2026.md`](../../docs/docs/proposals/feature-matrix-campaign-2026.md),
§5); the arm configs live in archi-config under `config/benchmarking/feature_matrix/`.

- **`lock_campaign.sh <00-baseline.yaml> --arms-dir <dir> --qa-dataset <qa-v2.json>`** — hashes every input
  the pre-registration pins (bank, anchors, prompt, sources, QA dataset and profile), the
  SUT and judge settings (agent class, model, base URL, sampling kwargs, context window, judge
  model and timeout, metrics), every non-factor `data_manager` setting, the sha256 of every
  arm YAML in `--arms-dir` keyed by label (each arm's treatment value is pinned), and the
  runtime code (git ids of `src/`, `scripts/`, `deploy/`, `pyproject.toml`, `requirements/`)
  into `bench_out/feature_matrix/campaign.lock`. Every other wrapper refuses an arm YAML,
  dataset, profile or spec whose content differs from the lock, a checkout whose runtime
  trees differ from the locked ones or that carries uncommitted source changes, an artifact
  whose run started under an earlier lock, and a stack deployed under an earlier lock
  (`run_arm.sh` stamps the lock into the deployment directory) — so acceptance depends on
  content, never on which file an operator named. A docs-only commit (the pre-registration)
  does not move the locked trees. Re-locking needs
  `--relock` and is recorded in the ledger.
- **`run_arm.sh <arm> <arm.yaml>`** — `archi evaluate -n fm-<arm> … --hostmode` (deploy,
  ingest, run). **`run_arm.sh <arm> --rerun`** re-runs only the benchmark container on the
  existing stack after proving the corpus fingerprint still equals the recorded pin.
- **`reseed_arm.sh <arm> <arm.yaml> [--stack fm-00] [--no-run]`** — switches a running stack
  to a retrieval-side arm without re-ingesting: copies the arm's `hierarchical_rerank` keys
  into the rendered config, re-runs `config-seed`, starts the benchmark container (`--no-run`
  re-seeds only — the way to restore the baseline without an unplanned run). Refuses an arm
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
  drifted; writes the corpus pin on run 1. The pin moves only for the closing baseline
  (`--new-corpus`: arm 00, after a fresh deploy, old pin recorded). Every wrapper refuses
  an arm label that does not match the YAML's own `name`. The live fingerprint is the
  harness's own routine (`CORPUS_STATE_QUERY` + `corpus_fingerprint`), run inside the
  data-manager container.
- **`test_feature_matrix_wrappers.sh`** — hermetic 44-check self-test (stubbed
  `docker`/`archi`, temp stack), run by `scripts/gate.sh`.

## Analysis and run helpers

The remaining scripts (notebooks, prompt-sweep generation, the Argilla push/reset
helpers, and `rebuild_benchmark.sh`) run and analyze the output of
`archi evaluate`.
