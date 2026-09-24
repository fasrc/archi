## 1. Library

- [ ] 1.1 `model: opus` — RED: self-test cases for `fm_require_stack_name`, `fm_sweep_lock_json` (three arms; hand-edited arm refused), `fm_category_map_digest` (stubbed docker prints a digest; failure prints `<unavailable:`) and `fm_require_sweep_lock` (one case per locked input changed: code tree, manifest, arm config, prompt, bank, anchors, QA dataset, profile, preparation; plus a missing stack stamp)
- [ ] 1.2 `model: opus` — GREEN: add the four functions to `lib.sh`; every sweep step calls `fm_require_sweep_lock` first

## 2. Prepare, lock and run (in this order: prepare → lock → run)

- [ ] 2.1 `model: opus` — RED/GREEN: new `qa_prepare.sh --sweep <stack>` — one `archi eval qa prepare` on the given dataset and profile into `<stack>-prepared`; refused when a sweep lock for the stack already exists
- [ ] 2.2 `model: opus` — RED/GREEN: `lock_campaign.sh --sweep` — regenerates the configs from the manifest and refuses on any stem or byte difference; requires the prepared workspace, and records its `preparation.jsonl` sha256; records the QA dataset and profile sha256; refuses without `--stack`; runs `category_census.py --exemplar-prompt` per arm with `## Worked examples` and records the disjointness result on that arm, refusing on a failure; the lock is written once
- [ ] 2.3 `model: opus` — RED/GREEN: `run_arm.sh --sweep` fresh form (every config and prompt matches the lock, `archi evaluate --config-dir`, stamp, ledger row) and `--sweep --rerun` (stamp, configs and prompts match, stack up, not in flight, corpus pin and map pin before and after, benchmark container only); prompt-edited and map-moved cases refused before any container is touched
- [ ] 2.4 `model: sonnet` — existing `run_arm.sh` campaign cases still pass unchanged

## 3. QA and archive

- [ ] 3.1 `model: opus` — RED/GREEN: `qa_arm.sh --sweep --arm <stem>` — refuses without the prepared workspace; copies it and runs `archi eval qa run` then `score` on the copy after checking its `preparation.jsonl` sha256; prompt, QA dataset and profile sha checked against the sweep lock, pin before/after, readings file written (success and `<unavailable:` cases), ledger `qa` row with both digests
- [ ] 3.2 `model: opus` — RED/GREEN: `archive_run.sh --sweep` — arm names match the locked stems one to one (duplicate, missing, extra refused); artifact older than the latest `ragas-start` or already in the ledger refused; a reused `(stem, stack, run)` refused; run ≥ 2 refused unless its usable readings equal the corpus and map pins; all rows and pins written in one step; every arm checked before any copy or ledger write (a failing third arm leaves no file and no row); per-arm endpoint checks; cross-arm fingerprint equality; snapshot files copied; missing snapshot refused; usable end digest with no named snapshot refused; snapshot hash ≠ end digest refused; changed or failed map archived with its state recorded; every arm's recorded `agent_md_sha256` equals its locked prompt sha256; one ledger row per arm; corpus pin and map pin (the census map digest) on run 1; run 1 requires `--census <json>` that passed, whose fingerprint equals the artifact's, whose map digest equals each arm's first usable map reading (`census_map_bound: false` recorded when an arm has none) and whose input digests and threshold equal the sweep lock's, recorded in each row; an arm whose prompt sha no longer equals its disjointness record is refused
- [ ] 3.3 `model: sonnet` — existing `qa_arm.sh` and `archive_run.sh` campaign cases still pass unchanged

## 4. Docs and verify

- [ ] 4.1 `model: haiku` — `scripts/benchmarking/README.md`: the sweep-mode commands in W8 order (prepare, lock, run, archive run 1 with the census, QA per arm, rerun, archive run 2, QA per arm)
- [ ] 4.2 `model: sonnet` — `bash scripts/gate.sh` green; `openspec validate sweep-mode-feature-matrix-wrappers --strict`

## 5. End-to-end check (after merge, per `AGENTS.md:58-63`)

- [ ] 5.1 `model: opus` — on the claw workstation, run a two-arm sweep over the 5-row anchor bank on an explicit stack (`benchmarking-<stack>`, `postgres-<stack>`, `data-manager-<stack>`) through prepare, lock, run, census, archive run 1, QA for one arm, rerun and archive run 2; confirm the image runs this checkout's source, then check the ledger rows, the copied snapshots and their hashes, the QA readings file and both pins; record the commands, outputs and logs on the PR
