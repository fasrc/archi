## Why

The fasrc-dev deploy scripts assume the `config/` checkout (fasrc/archi-config: source
lists, environments, agent prompts) already exists on the host — nothing clones or
updates it, so a fresh host deploys against a missing directory, and an existing host
deploys against whatever ref happens to be checked out (config is code-coupled, so an
unpinned version can silently mismatch the code). Worse, the live dev config still
points `sources.list` at the deleted sibling checkout (`~/Projects/archi-config/`), so
the next re-ingest would fail. This implements issue #99 with refreshed anchors (the
issue's SHAs predate the config repo's history rewrite).

## What Changes

- `deploy/fasrc-dev/scripts/lib.sh` gains `ensure_config`, called at the top of the
  shared deploy path so both `create.sh` and `redeploy.sh` provision `config/` before
  `archi create`: clone if absent; fetch + checkout the pinned `CONFIG_REF`
  (env-overridable annotated tag) only when the working tree is clean; a dirty tree is
  left byte-for-byte untouched with a loud warning (operators live-edit agent prompts;
  untracked question banks live there) unless `CONFIG_FORCE=1`, which stashes (never
  `reset --hard`/`clean`); verify expected files landed or die. Idempotent.
- An annotated pin tag is created in fasrc/archi-config at its current HEAD; the
  pin-bump procedure is documented in the `lib.sh` header and `.gitignore` comment.
- The dead sources path is fixed: host-local `deploy/fasrc-dev/config.yaml` (gitignored)
  repointed to the nested `config/` clone; tracked `config.example.yaml` updated to
  match so fresh hosts don't inherit the dead path.
- The battle-tested live dev config is synced into fasrc/archi-config as
  `environments/dev.yaml`, replacing the stale April template (repo reflects reality;
  the deployment still reads the host-local file — no behavior change).

## Capabilities

### New Capabilities
- `deploy-config-provisioning`: how a deploy provisions the external config repo —
  presence on fresh hosts, version pinning, dirty-tree (live-edit) safety, forced
  update semantics, and post-provision verification.

### Modified Capabilities
<!-- None. `config-propagation` (active change harden-config-propagation) covers a
     config change REACHING the running process; this capability covers the config
     BEING PRESENT at a known version. Both touch lib.sh — implementation-order note
     in design.md — but no requirement overlaps. -->

## Impact

- `deploy/fasrc-dev/scripts/lib.sh` (+ header docs), `deploy/fasrc-dev/config.example.yaml`,
  `.gitignore` comment — tracked, PR to `fasrc/archi:dev`, closes #99.
- Host-local edit (not in PR): `deploy/fasrc-dev/config.yaml` sources path.
- fasrc/archi-config: pin tag + `environments/dev.yaml` sync (committed to its `main`).
- Shell-only in fasrc/archi → gate's diff-cover has no Python lines to enforce; format +
  unit tests still must pass.
