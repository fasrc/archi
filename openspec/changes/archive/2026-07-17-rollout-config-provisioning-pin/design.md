# Design: rollout-config-provisioning-pin

## Context

PR #111 added `ensure_config` to `deploy/fasrc-dev/scripts/lib.sh`: every deploy
provisions `config/` (fasrc/archi-config) at a SHA-verified pin before `archi create`.
The dev host is not deployable under it. Measured state (2026-07-16):

```
fasrc/archi-config main            host config/ checkout (HEAD 944556f)
──────────────────────             ─────────────────────────────────────
990c54c  ← deploy-pin-2026-07      ahead 2, behind 20 vs the pin
   │  (history was REWRITTEN)          tracked: M sources.list, M dev.yaml,
   │  20 commits ahead                          D fasrc-cannon.md, D scripts/.env
   ▼                                  untracked: agent specs, benchmarking/,
 pinned content is STALE                        scripts/, compose.yaml, sources.list-old
```

The tracked-edits-on-off-pin-base state triggers the `die` branch of `ensure_config`.
And the pin's content is stale where it counts:

| | pin `990c54c7` | live working tree |
|---|---|---|
| KB pages | 195 | 219 |
| **git sources** | **0** | **2** (User_Codes, ood-documentation) |
| dev.yaml | live-synced | stale April template (working tree) |

`reset_collection: true` means every redeploy TRUNCATEs and re-embeds from the source
list; converging to this pin would drop the 724-file git corpus PR #108 just restored.
The real source list exists only as an uncommitted edit on this host.

Facts confirmed during exploration that de-risk the rollout:
- Host `config.yaml` already points `input_lists` at `config/lists/sources.list` (the
  dead-path fix from #111 is in place).
- This host stages `deploy/fasrc-dev/agents/` into the rendered dir; it does NOT consume
  `config/agents/` (verified in the lib.sh header). Agent-spec commits are for repo
  fidelity / other hosts, not this deployment's behavior.
- The live active agent is `FASRC-inline-v1`; `fasrc-inline-v1.md`,
  `fasrc-inline-v1-corrected-tools.md`, and the staged copy are byte-identical
  (`714aa4b4…`) — the "corrected tools" fix is already the canonical content.
- `fasrc-cannon.yaml` is 0 bytes (dead post-conversion artifact).

## Goals / Non-Goals

**Goals:**
- Make the dev host deployable under `ensure_config` at a pin whose content matches the
  live deployment (no corpus loss).
- Reconcile fasrc/archi-config `main` so the repo reflects reality (source list, current
  agent specs, benchmarking banks), without a history rewrite.
- Bump the pin in `lib.sh` via a PR to `fasrc/archi:dev`.
- Redeploy and verify corpus parity before the RAGAS re-baseline.

**Non-Goals:**
- No change to `ensure_config` logic (its behavior is correct; only the pinned content
  and the host's checkout state are wrong).
- No application/Python code changes.
- No history rewrite of fasrc/archi-config; the 2 stranded pre-rewrite commits are
  preserved only in the host backup (content fully superseded on disk).
- Not capturing the RAGAS baseline itself — that is the follow-on, gated on this.

## Decisions

**D1 — Reconcile by committing on top of the pin, not by rewriting.**
Branch fasrc/archi-config from `990c54c7` (the pin), apply the reconciled content, commit
to `main`. Rationale: the pin is on the current (post-rewrite) history line; building
forward from it keeps `git fetch --tags` honest and the new tag an ancestor-clean bump.

**D2 — Exact content manifest** (decided with the user):

| Path | Action | Why |
|---|---|---|
| `lists/sources.list` | commit live 370-line | only copy of the real manifest; has the 2 git sources |
| `agents/fasrc-inline-v1.md` | commit | canonical live SUT spec (= corrected-tools content) |
| `agents/fasrc-archi-v12.md` | commit | current chat dropdown spec |
| `agents/archive/**` | commit | 13 historical specs (hidden from dropdown) |
| `agents/fasrc-cannon.md` | MOVE → `archive/` | archive, not delete — other hosts may bind-mount it |
| `agents/fasrc-cannon.yaml` | delete | 0 bytes, pre-conversion dupe |
| `agents/fasrc-inline-v1-corrected-tools.md` | delete | byte-identical dupe of canonical |
| `scripts/.env` | delete | intentional; secrets hygiene |
| `benchmarking/**` | commit | shared measurement instrument |
| `environments/dev.yaml` | keep PIN's, discard working-tree | pin's is the live-synced one; working tree is the stale template |
| `scripts/` host additions, `compose.yaml`, `sources.list-old` | leave host-local | host-specific, not shared config |

**D3 — New tag name, never move the old one.** Mint `deploy-pin-2026-07b` (annotated) at
the reconciled commit; `git fetch --tags` refuses to clobber a moved tag, and the
ls-remote check in `ensure_config` treats a re-point as an abort. Bump `CONFIG_REF` +
`CONFIG_SHA` in `lib.sh` in one PR to `fasrc/archi:dev`.

**D4 — Swap the host checkout by clone-fresh, not stash-converge.** `mv config
config.pre-rewrite-bak`, let `ensure_config` clone fresh at the new pin. Rationale:
stashing tracked edits and converging *across a history rewrite* is fragile and leaves
confusing reflog state; a clean clone at a known pin is auditable. Restore any
kept-host-local untracked files (`scripts/`, `compose.yaml`) from the backup afterward.
The backup retains the 2 stranded commits and everything else, so nothing is lost.

**D5 — Verify content parity, not just deploy success.** After redeploy, assert
provenance `match=yes`, then compare corpus counts to the known-good baseline
(web ≈ 549, git 724 embedded / 13 failed) and a live chat 200. This is the new spec
requirement made operational.

**D6 — Strict ordering vs the RAGAS re-baseline.** The rollout's re-ingest re-crawls the
doc site and can absorb upstream drift; a baseline taken before rollout would be
measuring a corpus about to change. So: roll out, verify parity, THEN baseline.

## Risks / Trade-offs

- **A re-crawl absorbs upstream doc-site drift.** Same source list ≠ byte-identical
  corpus if docs.rc.fas pages changed since the last ingest. Mitigation: the parity
  check is on document *counts and source coverage*, and the baseline is taken from this
  post-rollout corpus — so the baseline measures exactly what's deployed. Accept that
  content drift is real and is precisely why D6 orders baseline after rollout.
- **Another host bind-mounts `config/agents/` live.** Moving `fasrc-cannon.md` to
  `archive/` changes its path. Mitigation: file still exists at a stable
  `agents/archive/fasrc-cannon.md`; only the dropdown-visibility changes, and this
  host's deployment is unaffected (D-context). Residual risk owned by whoever operates
  that other host — flag on the PR.
- **Deciding benchmarking banks belong in the config repo.** They are untracked today.
  Committing them makes the fork-vs-upstream benchmark reproducible (banks were the
  gitignored blocker noted for that work) but grows the repo. Trade-off accepted:
  reproducibility > repo size for a measurement instrument.
- **Shell-only fasrc/archi PR has no diff-cover signal.** `lib.sh` changes 2 constants;
  the gate's Python patch-coverage sees nothing. `test_ensure_config.sh` (added in #111)
  still exercises the provisioning contract against a fixture — run it locally as the
  substitute gate for the pin values.
- **~53 min re-ingest of downtime-ish state.** Chat degrades during re-embed. Acceptable
  on dev; do it outside any demo window.
