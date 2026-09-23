## MODIFIED Requirements

### Requirement: Version pinning
The checkout SHALL target a pinned ref `CONFIG_REF` (annotated tag; overridable via the
`CONFIG_REF` environment variable), never a floating branch, and `lib.sh` SHALL record
the expected commit id (`CONFIG_SHA`) alongside the tag name. The pin SHALL be recorded
per deployment: `lib.sh` SHALL hold a tracked table with one `CONFIG_REF`/`CONFIG_SHA`
row for each deployment name, and the deploy SHALL take the row that matches
`$DEPLOYMENT`, so a pin bump for one deployment does not change the pin of any other
deployment. The pin SHALL NOT be read from `host.env`. An explicit `CONFIG_REF` and
`CONFIG_SHA` pair in the environment SHALL take precedence over the table; if the
environment sets only one of the two keys, `ensure_config` SHALL abort before it
provisions, naming the key that is set, and SHALL NOT fill the other key from the
table. If no row matches
`$DEPLOYMENT` and the environment gives no pin, `ensure_config` SHALL abort before it
clones or checks out anything, naming the deployment; sourcing `lib.sh` SHALL NOT abort
for that reason. After fetch, the deploy SHALL verify the tag resolves to `CONFIG_SHA`
and abort on mismatch (a re-pointed remote tag is treated as an attack or mistake, never
followed). On provisioned hosts the REMOTE tag object SHALL be checked directly
(`git ls-remote`) so a re-point aborts even though `git fetch --tags` refuses to clobber
the correct local tag; an unreachable origin is tolerated with a warning (local
verification still applies). On a clean working tree the deploy SHALL converge `config/`
to that ref. If the ref does not resolve after fetch, the deploy SHALL abort.

#### Scenario: Clean tree converges to the pin
- **WHEN** `config/` has a clean working tree and HEAD differs from `CONFIG_REF`
- **THEN** after `ensure_config`, `git -C config rev-parse HEAD` equals
  `git -C config rev-parse CONFIG_REF^{commit}`

#### Scenario: Unresolvable ref aborts
- **WHEN** `CONFIG_REF` names a ref that does not exist
- **THEN** the deploy dies before `archi create`, naming the bad ref

#### Scenario: Re-pointed tag aborts
- **WHEN** the remote tag `CONFIG_REF` resolves to a commit other than `CONFIG_SHA`
- **THEN** the deploy dies before `archi create`, naming both commit ids

#### Scenario: A pin bump for one deployment leaves the other unchanged
- **WHEN** the `claw` row of the pin table names a new tag and commit
- **THEN** a `DEPLOYMENT=claw` deploy resolves the new pin, and a `DEPLOYMENT=dev`
  deploy still resolves the `dev` row's pin

#### Scenario: A deployment with no pin row aborts at provisioning
- **WHEN** `DEPLOYMENT` names a deployment with no row and no `CONFIG_REF`/`CONFIG_SHA`
  is in the environment
- **THEN** sourcing `lib.sh` succeeds, and `ensure_config` dies naming the deployment
  before it creates `config/`

#### Scenario: The command-line pin overrides the table
- **WHEN** `CONFIG_REF` and `CONFIG_SHA` are in the environment
- **THEN** the deploy uses them for any deployment name, with or without a table row

#### Scenario: A one-key override aborts
- **WHEN** the environment sets `CONFIG_REF` without `CONFIG_SHA`, or `CONFIG_SHA`
  without `CONFIG_REF`
- **THEN** sourcing `lib.sh` succeeds, and `ensure_config` dies naming the key that is
  set, before it creates `config/`
