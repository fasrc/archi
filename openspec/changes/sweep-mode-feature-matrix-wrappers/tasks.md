## 1. Library

- [ ] 1.1 `model: opus` — RED: self-test cases for `fm_require_stack_name`, `fm_sweep_lock_json` (three arms; hand-edited arm refused) and `fm_category_map_digest` (stubbed docker prints a digest; failure prints `<unavailable:`)
- [ ] 1.2 `model: opus` — GREEN: add the three functions to `lib.sh`

## 2. Lock and run

- [ ] 2.1 `model: opus` — RED/GREEN: `lock_campaign.sh --sweep` regenerates the configs from the manifest and refuses on any stem or byte difference; writes `sweep-<stack>.lock` with the QA dataset and profile sha256; refuses without `--stack`; runs `category_census.py --exemplar-prompt` per arm with `## Worked examples` and records the disjointness result on that arm, refusing on a failure
- [ ] 2.2 `model: opus` — RED/GREEN: `run_arm.sh --sweep` fresh form (lock matches, `archi evaluate --config-dir`, stamp, ledger row) and `--sweep --rerun` (stamp, stack up, not in flight, pin before/after, benchmark container only)
- [ ] 2.3 `model: sonnet` — existing `run_arm.sh` campaign cases still pass unchanged

## 3. QA and archive

- [ ] 3.0 `model: opus` — RED/GREEN: new `qa_prepare.sh --sweep <stack>` — one `archi eval qa prepare` on the locked dataset, `preparation.jsonl` sha256 recorded in the sweep lock; refuses a second prepare unless `--force`
- [ ] 3.1 `model: opus` — RED/GREEN: `qa_arm.sh --sweep --arm <stem>` — refuses without the prepared workspace; copies it and runs `archi eval qa run` then `score` on the copy after checking its `preparation.jsonl` sha256; prompt, QA dataset and profile sha checked against the sweep lock, pin before/after, readings file written (success and `<unavailable:` cases), ledger `qa` row with both digests
- [ ] 3.2 `model: opus` — RED/GREEN: `archive_run.sh --sweep` — multi-arm accepted, every arm checked before any copy or ledger write (a failing third arm leaves no file and no row), per-arm endpoint checks, cross-arm fingerprint equality, snapshot files copied, missing snapshot refused, usable end digest with no named snapshot refused, snapshot hash ≠ end digest refused, census bound to each arm's first usable reading (`census_map_bound: false` recorded when an arm has none), changed or failed map archived with its state recorded, one ledger row per arm, pin on run 1; run 1 requires `--census <json>` that passed, whose fingerprint equals the artifact's, whose map digest equals every arm's start digest and whose input digests and threshold equal the sweep lock's, recorded in each row; an arm whose prompt sha no longer equals its disjointness record is refused
- [ ] 3.3 `model: sonnet` — existing `qa_arm.sh` and `archive_run.sh` campaign cases still pass unchanged

## 4. Docs and verify

- [ ] 4.1 `model: haiku` — `scripts/benchmarking/README.md`: the sweep-mode commands in W8 order
- [ ] 4.2 `model: sonnet` — `bash scripts/gate.sh` green; `openspec validate sweep-mode-feature-matrix-wrappers --strict`
