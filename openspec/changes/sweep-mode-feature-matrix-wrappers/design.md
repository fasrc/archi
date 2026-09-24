## Context

The feature-matrix wrappers (`scripts/benchmarking/feature_matrix/`, `origin/dev` 5d72ed49)
model one arm per stack (`fm-<arm>`), one campaign lock with one prompt, and one arm per
artifact. A prompt sweep is one stack running every arm in one `archi evaluate --config-dir`
invocation (`generate_prompt_sweep.py` renders one config per prompt, identical except
`agent_md_file`, `name` and `primary_metric`), so the artifact carries every arm
(`service_benchmark.py:695`). The primitives the wrappers are built from still fit:
`fm_fingerprint` (`lib.sh:85-99`, runs the harness's own query inside the data-manager),
`fm_require_stack_up`, `fm_container_state`, `fm_require_code_lock`, `fm_ledger_append`, the
pin file, and `test_feature_matrix_wrappers.sh`, which `scripts/gate.sh` runs.

## Goals / Non-Goals

**Goals:** a `--sweep` mode on `lock_campaign.sh`, `run_arm.sh`, `qa_arm.sh` and
`archive_run.sh`; per-arm prompt pinning; QA-run category-map readings; multi-arm archive.

**Non-Goals:** changing any non-`--sweep` path; the slice and gates (consumer change); the
map digest itself (producer change); running the sweep (plan W8, human-gated).

## Decisions

1. **A mode, not new scripts.** Each wrapper branches on `--sweep` early and reuses the same
   primitives; the plan says W6 extends this path. The non-sweep branch is untouched, and the
   existing self-test cases stay as they are. *Alternative:* a separate `prompt_sweep/`
   directory — rejected by the plan's wording and by the operator's choice of this split.
2. **The sweep lock replaces the campaign lock only in sweep mode.** It is a separate file
   (`sweep-<stack>.lock`) so a running campaign's `campaign.lock` is never read or rewritten by
   a sweep. The stack stamp reuses `fm-lock.sha256` with the sweep lock's hash, so
   `fm_require_stack_lock`-style checks work with a lock-file argument.
3. **One arm identity everywhere: the prompt stem.** `generate_prompt_sweep.py` writes each arm
   as `<prompt-stem>.yaml` and sets `services.benchmarking.name` to the same stem (`:127-133`),
   and the artifact records that name in each arm's `configuration`. `qa_arm.sh --arm`, the
   sweep lock's arm keys and the ledger rows all use it, and `compare_runs.py` resolves the
   same string through its recorded-name selector (consumer change), so no second mapping
   exists.
4. **Stack names are validated, not derived.** `--stack` is required in sweep mode and must
   match `^[a-z0-9][a-z0-9-]{0,40}$`, the same concern `fm_require_arm` guards (the name
   reaches deployment paths).
5. **The category-map reading runs like `fm_fingerprint`**: a `python -c` snippet executed in
   `data-manager-<stack>` that imports the shared helper from `src.utils.benchmark_provenance`
   and prints the digest, printing `<unavailable: …>` on any exception instead of failing the
   wrapper — the three-state contract of #538 rule 2 lives in the consumer.
6. **Per-arm prompt sha goes into the ledger row**, so the "every arm's prompt hashed per run"
   void check in plan §5 is answered from the ledger, not re-derived later.
7. **Pin on run 1 from the artifact**, as the campaign's `archive_run.sh` does, after checking
   all arms agree on one fingerprint.

## Risks / Trade-offs

- [Weakening a campaign guard by accident] → every sweep branch sits after an explicit
  `--sweep` parse; the self-test runs the full existing campaign case list unchanged plus new
  sweep cases.
- [Image without the digest helper] → the reading snippet fails closed to `<unavailable: …>`
  with a message naming the missing import, and the consumer then refuses the QA join.
- [Bash + inline Python grows] → keep each new block a small function in `lib.sh` with a
  self-test case; no logic duplicated across wrappers.
