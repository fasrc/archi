# Proposal: rollout-config-provisioning-pin

## Why

PR #111 (`edc0549d`, capability `deploy-config-provisioning`) made every dev deploy
provision the `config/` checkout (fasrc/archi-config) at a SHA-verified pin — but the
dev host cannot actually deploy under it yet. The host's checkout is stranded on
pre-history-rewrite commits (2 unpushed, 20 behind the pin) with tracked live edits,
which hits the new code's refuse-to-deploy branch: the next `redeploy.sh` hard-dies.
Worse, the pin itself (`deploy-pin-2026-07` → `990c54c7`) is stale where it matters
most: its `lists/sources.list` has **zero git sources** and 24 fewer KB pages than the
live list, so converging to it and re-ingesting (every redeploy re-ingests;
`reset_collection: true`) would silently drop the entire 724-file git corpus — the
User_Codes examples PR #108 just restored. The only copy of the real source list is an
uncommitted working-tree edit on this host. This must be reconciled before the pending
RAGAS re-baseline, or the rollout's re-ingest would invalidate it.

## What Changes

- **fasrc/archi-config content reconciliation** (its `main` branch, from the pin commit):
  - Commit the live 370-line `lists/sources.list` (219 KB pages + the 2 `git-` sources).
  - Commit the current-generation agent specs: `agents/fasrc-inline-v1.md` (canonical;
    content already equals the "corrected-tools" variant and matches the live active
    agent `FASRC-inline-v1`) and `agents/fasrc-archi-v12.md`.
  - Commit `agents/archive/` (13 historical specs) and move `agents/fasrc-cannon.md`
    into it (archive, don't delete — other hosts may bind-mount `config/agents/`).
  - Delete: empty `agents/fasrc-cannon.yaml`, byte-identical duplicate
    `agents/fasrc-inline-v1-corrected-tools.md`, and `scripts/.env` (confirmed
    intentional; secrets hygiene).
  - Commit the `benchmarking/` question banks (shared measurement instrument).
  - Leave host-local: `scripts/` additions, `compose.yaml`, `lists/sources.list-old`.
- **New pin**: mint a NEW annotated tag in fasrc/archi-config at the reconciled commit
  (never move the existing tag — documented pin-bump procedure).
- **fasrc/archi PR**: bump `CONFIG_REF` + `CONFIG_SHA` in
  `deploy/fasrc-dev/scripts/lib.sh` in the same PR (base `dev`).
- **Dev host rollout**: sync local `dev` to origin/dev; back up the stranded `config/`
  checkout and let `ensure_config` clone fresh at the new pin (cleaner than stashing
  across a history rewrite); restore kept-host-local untracked files from the backup;
  run `redeploy.sh`; verify provenance line `match=yes`, corpus counts
  (web ≈549, git 724 embedded / 13 failed), and a live chat 200.
- **Sequencing**: complete this rollout BEFORE capturing the RAGAS re-baseline.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `deploy-config-provisioning`: ADDED requirement — a pin bump must preserve the live
  deployment's source manifest (the pinned `lists/sources.list` must be content-verified
  against the list that produced the current corpus, git sources included) before the
  new pin is deployed. This is the spec-level lesson of the stale-pin trap found during
  rollout: the existing requirements verify the checkout's *version and shape*, nothing
  verifies its *content parity* with the running deployment.

## Impact

- **fasrc/archi**: `deploy/fasrc-dev/scripts/lib.sh` (2 pinned values) — shell-only, so
  the gate's diff-cover has no Python lines to enforce; format + unit tests still run.
  PR to `fasrc/archi:dev`.
- **fasrc/archi-config**: content commits on `main` + one new annotated tag. No history
  rewrite.
- **Dev host**: `config/` checkout replaced at the new pin (backup retained); one full
  redeploy with ~53 min re-ingest; corpus content should be identical (same source
  list), but a re-crawl can pick up upstream doc-site drift — hence the sequencing
  constraint with the RAGAS re-baseline.
- **Not in scope**: no application code changes; no changes to `ensure_config` logic
  itself; the two stranded pre-rewrite commits in the old checkout are preserved only
  in the backup (content fully superseded on disk).
