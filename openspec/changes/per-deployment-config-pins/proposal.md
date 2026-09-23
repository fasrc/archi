# Give each deployment its own config pin

## Why

Two hosts deploy archi from this repository: the GPU host (deployment `dev`) and the
claw workstation (deployment `claw`). Each host already has its own identity through
`host.env` (issue #363), but both read one config pin from the tracked
`deploy/scripts/lib.sh` (verified on `origin/dev` at `f6d87836`):

```bash
141:CONFIG_REF="${CONFIG_REF:-deploy-pin-2026-09d}"
142:CONFIG_SHA="${CONFIG_SHA:-9c3da1d064152089cbd4a38ebb81bcfbce61c469}"
```

As a result, a pin bump for one host moves every host. PR #541 shows the cost: it bumps
the pin only to make the rung-0 prompt sweep files reachable on claw, and it must carry
a CAUTION that the GPU host also converges to the new pin on its next deploy. A change
for a benchmark on a workstation must not change the config of the GPU host.

`host.env` is not the fix. It deliberately refuses `CONFIG_REF`/`CONFIG_SHA`
(`host.env.example:35-37`), because a pin in a git-excluded file is invisible to review.
That reason still holds, so the pins stay in the tracked `lib.sh`.

## What Changes

- `lib.sh` replaces the single `CONFIG_REF`/`CONFIG_SHA` default with a tracked pin
  table keyed by `$DEPLOYMENT`: one row for `dev` and one row for `claw`. A pin bump
  edits one row, and only that deployment converges to the new pin.
- Both rows start at today's pin (`deploy-pin-2026-09d` / `9c3da1d0`), so this change
  alters no deployed config.
- A deployment name with no row gets no pin. `ensure_config` then aborts before it
  clones or checks out anything, and names the deployment. Sourcing `lib.sh` does not
  abort, so `status.sh` and `nuke.sh` still work for that name.
- The command-line override (`CONFIG_REF=... CONFIG_SHA=... ./redeploy.sh`) still wins
  over the table, for any deployment name.
- `host.env` still refuses the pin keys. No change to `test_host_env.sh` case 7.
- Docs that describe "the pin" as one value are updated to describe the table.

## Impact

- Affected spec: `deploy-config-provisioning` (requirement "Version pinning").
- Affected code: `deploy/scripts/lib.sh`, `deploy/scripts/test_ensure_config.sh`,
  `deploy/scripts/host.env.example`, `docs/docs/fasrc_archi.md`, `docs/docs/glossary.md`.
- PR #541 must be rebased to change only the `claw` row, and its CAUTION removed.
