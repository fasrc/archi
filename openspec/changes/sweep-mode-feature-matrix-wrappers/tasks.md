## 1. Library

- [ ] 1.1 `model: opus` — RED: self-test cases for `fm_require_stack_name`, `fm_sweep_lock_json` (three arms; hand-edited arm refused) and `fm_category_map_digest` (stubbed docker prints a digest; failure prints `<unavailable:`)
- [ ] 1.2 `model: opus` — GREEN: add the three functions to `lib.sh`

## 2. Lock and run

- [ ] 2.1 `model: opus` — RED/GREEN: `lock_campaign.sh --sweep` writes `sweep-<stack>.lock`; refuses without `--stack`; runs `category_census.py --exemplar-prompt` per arm with `## Worked examples` and records the disjointness result on that arm, refusing on a failure
- [ ] 2.2 `model: opus` — RED/GREEN: `run_arm.sh --sweep` fresh form (lock matches, `archi evaluate --config-dir`, stamp, ledger row) and `--sweep --rerun` (stamp, stack up, not in flight, pin before/after, benchmark container only)
- [ ] 2.3 `model: sonnet` — existing `run_arm.sh` campaign cases still pass unchanged

## 3. QA and archive

- [ ] 3.1 `model: opus` — RED/GREEN: `qa_arm.sh --sweep --arm <stem>` — prompt sha checked against the sweep lock, pin before/after, readings file written (success and `<unavailable:` cases), ledger `qa` row with both digests
- [ ] 3.2 `model: opus` — RED/GREEN: `archive_run.sh --sweep` — multi-arm accepted, per-arm endpoint checks, cross-arm fingerprint equality, snapshot files copied, missing snapshot refused, one ledger row per arm, pin on run 1; run 1 requires `--census <json>` that passed, whose fingerprint equals the artifact's, whose map digest equals every arm's start digest and whose input digests and threshold equal the sweep lock's, recorded in each row; an arm whose prompt sha no longer equals its disjointness record is refused
- [ ] 3.3 `model: sonnet` — existing `qa_arm.sh` and `archive_run.sh` campaign cases still pass unchanged

## 4. Docs and verify

- [ ] 4.1 `model: haiku` — `scripts/benchmarking/README.md`: the sweep-mode commands in W8 order
- [ ] 4.2 `model: sonnet` — `bash scripts/gate.sh` green; `openspec validate sweep-mode-feature-matrix-wrappers --strict`
