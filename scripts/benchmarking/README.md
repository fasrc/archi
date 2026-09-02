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

## Analysis and run helpers

The remaining scripts (notebooks, prompt-sweep generation, the Argilla push/reset
helpers, and `rebuild_benchmark.sh`) run and analyze the output of
`archi evaluate`.
