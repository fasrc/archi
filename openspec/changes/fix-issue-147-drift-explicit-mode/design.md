## Context

`scripts/benchmarking/goldenset_maintenance.py` exposes a `drift` subcommand that re-hashes
every locked bank row's grounding pages (the tripwire) and, on a hash mismatch, optionally asks
an LLM whether the stored `reference` still holds (the semantic pass). The semantic pass is
selected today by *presence of* `--model`: `run_drift` (line 775) computes
`ask_llm = build_ask_llm(args.model) if args.model else None`. Omitting `--model` silently
yields a hash-only run that reports moved hashes, prints a qualifying NOTE **only when there are
drifted rows** (line 870), and exits 0. A no-drift hash-only run is indistinguishable from a
completed semantic check. Issue #147 asks that the mode be *chosen*, not defaulted.

A structural constraint dominates the design: `run_drift` is reused verbatim by the `report`
subcommand (the group-5 read-only dev-server cron) via `_REPORT_PASSES` (line 994). `report`
runs drift hash-only by default and exposes its **own optional** `--model` (line 1266); it
already declares inert defaults for flags its reused runners read but it does not expose, in one
place — `report.set_defaults(...)` (line ~1288). Any change to `run_drift`'s mode resolution
must leave `report`'s behavior byte-for-byte unchanged, or it breaks the cron.

## Goals / Non-Goals

**Goals:**
- Make the standalone `drift` subcommand require the operator to choose exactly one of
  `--model` (semantic) or `--tripwire-only` (hash-only).
- Preserve the hash-only path — it is the cron's deliberate default, not a bug.
- State the selected mode in the drift report header, on every run, not only when drift is found.
- Leave the `report` (group-5 cron) drift pass identical, including its optional `--model`.

**Non-Goals:**
- Changing what the tripwire or the semantic pass *does*, or the drift/orphan/coverage logic.
- Adding `--tripwire-only` to the `report` subcommand (its optional `--model` already
  expresses hash-only-by-omission for the cron; adding a second lever would be redundant).
- Removing or altering the standalone `--show-text` / `--print-hashes` flags.

## Decisions

**D1 — Enforce the required choice in the argparse layer, on the drift subparser only.**
Move `--model` into a `drift.add_mutually_exclusive_group(required=True)` alongside a new
`--tripwire-only` (`action="store_true"`), following the `coverage`/`report` subcommands'
existing `add_mutually_exclusive_group()` shape. argparse then enforces "exactly one":
- neither → exit 2, message "one of the arguments --model --tripwire-only is required"
  (names **both**, satisfying the acceptance criterion);
- both → exit 2, "argument --tripwire-only: not allowed with argument --model".
*Alternative rejected:* hand-rolled validation inside `run_drift`. That would also run for the
`report` pass (shared runner) and reintroduce the exact ambiguity — validation belongs where
only the standalone command sees it.

**D2 — Resolve the mode in `run_drift` defensively, so `report` is untouched.**
Replace line 775 with:
```python
tripwire_only = getattr(args, "tripwire_only", False)
ask_llm = None if tripwire_only else (build_ask_llm(args.model) if args.model else None)
```
`getattr(..., False)` means the `report` pass — whose namespace has no `tripwire_only` unless we
add one — keeps deriving the mode from its own optional `--model` exactly as before. Add
`tripwire_only=False` to `report.set_defaults(...)` as well, matching the file's stated idiom
of naming every inert reused-runner flag in one visible place (the comment at line ~1284).
*Alternative rejected:* hardcoding `tripwire_only=True` for the report pass — that would
suppress `report --model`'s semantic diff, silently breaking a real cron feature.

**D3 — Print an unconditional mode header at the top of `run_drift`.**
Emit one line stating whether the run is the hash-only tripwire or the reference-compared
semantic pass, before the existing `locked rows: …` summary. This makes a clean hash-only run
declare its own limits on its face — the current NOTE (line 870) only fires when rows drifted.
The existing NOTE stays as-is (it adds the "re-run with --model" remedy beside the findings).

## Risks / Trade-offs

- [Breaking the no-flag `drift` invocation] → Intended per the issue; `drift` shipped in
  `e3bbb55f` with no cron/script consumer. Mitigation: the tasks include the issue's
  `grep -rn "goldenset_maintenance.py drift"` sweep to confirm no `.sh`/`.yaml`/`.md` caller
  relies on the old default before relying on the break.
- [Silently breaking the group-5 cron] → the shared-runner seam is the whole risk. Mitigation:
  D2's defensive read + explicit `report.set_defaults(tripwire_only=False)`, plus a regression
  assertion that `report` with no `--model` still runs drift hash-only and exits 0.
- [Docs drift] → `docs/docs/benchmarking.md` drift examples must show the deliberate choice and
  every example must carry `--allowed-hosts`; a task asserts the two grep counts are equal.

## Migration Plan

No data or deploy migration. Ship as a single PR to `dev`. Rollback = revert the PR; the
hash-only path is unchanged internally, so no state is affected. The group-5 cron config is
untouched (it invokes `report`, not `drift`).

## Open Questions

None — the issue specifies the two-mode contract and the constraint to keep the hash-only path.
