## ADDED Requirements

### Requirement: Deploys provision the config checkout
Both `create.sh` and `redeploy.sh` SHALL run `ensure_config` before `archi create`:
if `$REPO_ROOT/config/.git` is absent, clone `fasrc/archi-config` there and check out
`CONFIG_REF`; if present, fetch (with tags) from origin. The function SHALL be
idempotent across repeated deploys.

#### Scenario: Fresh host
- **WHEN** a deploy runs on a host with no `config/` directory
- **THEN** `config/` is cloned and checked out at `CONFIG_REF` before `archi create` runs

#### Scenario: Repeated deploys
- **WHEN** `redeploy.sh` runs twice in a row with no other changes
- **THEN** the second run succeeds and leaves `config/` in the same state as the first

### Requirement: Version pinning
The checkout SHALL target a pinned ref `CONFIG_REF` (annotated tag; overridable via the
`CONFIG_REF` environment variable), never a floating branch. On a clean working tree the
deploy SHALL converge `config/` to that ref. If the ref does not resolve after fetch,
the deploy SHALL abort.

#### Scenario: Clean tree converges to the pin
- **WHEN** `config/` has a clean working tree and HEAD differs from `CONFIG_REF`
- **THEN** after `ensure_config`, `git -C config rev-parse HEAD` equals
  `git -C config rev-parse CONFIG_REF^{commit}`

#### Scenario: Unresolvable ref aborts
- **WHEN** `CONFIG_REF` names a ref that does not exist
- **THEN** the deploy dies before `archi create`, naming the bad ref

### Requirement: Dirty-tree safety
A dirty `config/` working tree (any modified, deleted, or untracked path) SHALL NOT be
modified by a default deploy run: no checkout, no reset, no clean. The deploy SHALL
print a loud warning naming the dirty paths and continue with the on-disk config. With
`CONFIG_FORCE=1`, the deploy SHALL preserve the dirty state via `git stash -u` (printing
the recovery command) before checking out the pin; `git reset --hard` and `git clean`
SHALL NOT be used under any path.

#### Scenario: Dirty tree untouched by default
- **WHEN** `config/` has a modified tracked file and an untracked file and a default
  deploy runs
- **THEN** both files are byte-for-byte unchanged afterward and the deploy proceeded
  using the on-disk config

#### Scenario: Forced update stashes, never destroys
- **WHEN** the same dirty tree is deployed with `CONFIG_FORCE=1`
- **THEN** `config/` is at `CONFIG_REF`, the edits exist in a stash entry, and the
  warning printed the `git -C config stash pop` recovery command

### Requirement: Post-provision verification
After provisioning, the deploy SHALL verify that `lists/sources.list`,
`environments/dev.yaml`, and the `agents/` directory exist under `config/`, and abort
before `archi create` if any is missing.

#### Scenario: Partial or wrong checkout aborts
- **WHEN** the checked-out ref lacks `lists/sources.list`
- **THEN** the deploy dies naming the missing file, and `archi create` never runs

### Requirement: Sources path resolves to the provisioned checkout
The deploy config's ingestion source lists SHALL reference paths under the provisioned
`$REPO_ROOT/config/` checkout (not a sibling or absent directory), and the tracked
`config.example.yaml` SHALL model the same path for fresh hosts.

#### Scenario: Re-ingest finds sources.list
- **WHEN** the data manager resolves the configured sources list path on the dev host
- **THEN** the file exists and is the one provisioned by `ensure_config`
