## Why

The rung-0 sweep (#540) needs two replicates on one stack, QA runs per arm, and archived
artifacts — and every wrapper that does those things refuses a sweep. `run_arm.sh --rerun`,
`qa_arm.sh` and `archive_run.sh` require a two-digit feature-matrix arm label
(`scripts/benchmarking/feature_matrix/lib.sh:29`) and a campaign lock that hashes **one**
prompt for the whole campaign (`lib.sh:155`, `:158-161`), which a prompt sweep varies by
design; `archive_run.sh` also refuses any artifact with more than one arm (`:112-113`). The
plan says W6 "must extend that path rather than invent one"
(`docs/docs/proposals/categories-action-plan.md` Phase 1), and #538 rule 1 requires a
category-map reading before and after each QA run, taken by the wrapper that starts it.

## What Changes

- **A sweep lock** (`lock_campaign.sh --sweep <sweep_dir> --manifest <yaml>` → `$FM_OUT/sweep-<stack>.lock`):
  the sha256 of the manifest, of every generated arm config and of each arm's
  `agent_md_file`, plus the bank, anchors and the locked code tree — the per-arm prompt
  manifest the plan's §5 void checks require.
- **`run_arm.sh --sweep <sweep_dir> --stack <name>`**: the first replicate
  (`archi evaluate --config-dir … --name <stack> --hostmode`), stamping the stack with the
  sweep lock; and **`--sweep … --rerun`**: recreate only the benchmark container, between
  two corpus-pin checks, as the campaign's `--rerun` does.
- **`qa_prepare.sh --sweep <stack>`** (new): prepares the gold atoms once per sweep and pins
  `preparation.jsonl` in the sweep lock, as plan W8 requires ("Prepare the gold atoms once and
  run every arm against that one snapshot"; each `archi eval qa` invocation otherwise
  re-extracts atoms, `src/evaluation/qa/preparation.py:305-320`).
- **`qa_arm.sh --sweep <sweep_dir> --stack <name> --arm <config-stem>`**: the QA run for one
  sweep arm with that arm's own prompt, on a copy of the prepared workspace
  (`archi eval qa run` + `score`), with the prompt, QA dataset and profile hash-checked against
  the sweep lock, a corpus-pin
  check before and after, and a category-map reading before and after written to
  `<qa_dir>/category_map_readings.json` (`{"start", "end"}`), which `compare_runs.py` reads for
  the QA join rule.
- **`archive_run.sh --sweep <sweep_dir> --stack <name>`**: accepts a multi-arm artifact, runs
  the endpoint checks per arm, copies the artifact, its report and every
  `_category_map_<N>.tsv`, and appends one `ragas` ledger row per arm carrying
  `category_map_sha256_start`, `category_map_sha256_end`, `category_map_file` and the arm's
  prompt sha256; replicate 1 writes the corpus pin.
- Feature-matrix behavior without `--sweep` is unchanged; the self-test proves it.

## Capabilities

### New Capabilities
<!-- none: category-slice-provenance is introduced by record-category-map-digest -->

### Modified Capabilities
- `category-slice-provenance`: adds the harness requirements (sweep lock, sweep rerun, QA
  map readings, multi-arm archive).

## Impact

- `scripts/benchmarking/feature_matrix/{lib.sh,lock_campaign.sh,run_arm.sh,qa_arm.sh,archive_run.sh}`
  and `test_feature_matrix_wrappers.sh` (new cases); `scripts/benchmarking/README.md`.
- The category-map reading inside the data-manager container reuses the helper from
  `record-category-map-digest`, so the stack image must carry that change (the sweep is
  deployed after it merges).
- Uses `category_census.py` from `preflight-category-census` (disjointness at lock time, the
  census JSON at archive time); order: producer, census, consumer, this change.
- No Python behavior outside the reading snippet; no effect on a running campaign.
