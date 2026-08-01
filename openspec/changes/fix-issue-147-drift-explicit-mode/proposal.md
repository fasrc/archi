## Why

The goldenset `drift` subcommand runs two stages — a cheap hash tripwire, then (on a hash
mismatch) an LLM asked whether the stored `reference` still holds. Today the second, semantic
stage is chosen by *omission*: `ask_llm = build_ask_llm(args.model) if args.model else None`.
Omitting `--model` silently downgrades `drift` to hash-only — it reports every moved hash with
no verdict and exits 0, so a run that skipped the semantic half looks complete. An operator who
read the docs' "hash tripwire → LLM diff" description reasonably mistakes that report for the
full check. This resolves fasrc/archi#147 (from a post-merge adversarial review of PR #144).

## What Changes

- Add a `--tripwire-only` flag to the `drift` subcommand that explicitly requests the
  hash-only pass (no LLM call).
- Make `drift` require the operator to choose **exactly one** of `--model` (semantic) or
  `--tripwire-only` (hash-only), via a required mutually-exclusive group — mirroring the
  `coverage` subcommand's existing `add_mutually_exclusive_group()` shape.
- **BREAKING** (intended, acceptable): the current no-flag `drift` invocation now exits
  non-zero with a message naming both modes. `drift` shipped in `e3bbb55f` with no cron or
  script consumer yet, so nothing depends on the implicit default.
- Label the selected mode in the drift report header, so a tripwire-only run states on its
  face that the semantic pass was not run — not merely by the absence of verdicts.
- The hash-only path is **preserved**, now reached explicitly. Group 5's read-only dev-server
  cron continues to use it (an LLM call per drifted row on every tick is the wrong default).
- Update `docs/docs/benchmarking.md` so both drift examples present `--tripwire-only` and
  `--model` as a deliberate choice and state which mode the group 5 cron uses; every drift
  example carries the required `--allowed-hosts` flag.

## Capabilities

### New Capabilities
- `goldenset-drift-mode`: the CLI contract for the goldenset `drift` subcommand's mode
  selection — the operator must explicitly choose the semantic (`--model`) or hash-only
  (`--tripwire-only`) drift pass, the two are mutually exclusive and one is required, and the
  report header names the chosen mode. Refines drift behavior established in the active
  `maintain-ragas-goldenset` change (not yet archived), expressed additively as the
  mode-selection contract.

### Modified Capabilities
<!-- None: the base drift requirement lives in the unarchived maintain-ragas-goldenset change,
     not in openspec/specs/, so this refinement is expressed as a new additive capability. -->

## Impact

- `scripts/benchmarking/goldenset_maintenance.py` — `build_parser()` (drift subparser flags)
  and `run_drift()` (mode resolution + report header).
- `tests/unit/test_goldenset_maintenance_script.py` — new mode-selection tests.
- `docs/docs/benchmarking.md` — drift examples updated for explicit mode + `--allowed-hosts`.
- No production/runtime code, no deploy, no dependency changes. CLI-surface change only.
