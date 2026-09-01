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

- **`measure_chunk_overlap.py`** — measures the overlap a sentence splitter
  *actually* carries across chunk boundaries, for a sweep of `chunk_overlap`
  values. `chunk_overlap` is a budget, not a guarantee: the splitter copies back
  only whole sentences that fit, so a budget below one sentence often carries
  nothing. Reports empty boundaries, tokens carried, and the index inflation each
  setting costs. Takes a text file of real corpus content (the docstring has the
  `psql` dump command) and needs no deployment — just the database dump and the
  project env. Supports the #396 feature matrix and the `chunking.chunk_overlap`
  key (#403).

## Analysis and run helpers

The remaining scripts (notebooks, prompt-sweep generation, the Argilla push/reset
helpers, and `rebuild_benchmark.sh`) run and analyze the output of
`archi evaluate`.
