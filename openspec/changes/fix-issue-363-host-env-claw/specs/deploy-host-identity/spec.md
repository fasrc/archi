## ADDED Requirements

### Requirement: A host pins its deployment identity without editing tracked files

`deploy/fasrc-dev/scripts/lib.sh` SHALL read an optional, git-excluded
`deploy/fasrc-dev/scripts/host.env` after `REPO_ROOT` is resolved and before the
deployment-identity defaults are applied, and SHALL honor overrides of `DEPLOYMENT` and
`CONFIG`, with the checked-in defaults `dev` and `deploy/fasrc-dev/config.yaml`. A
missing `host.env` SHALL NOT be an error: a host that writes no file resolves exactly
the values the tracked file ships, which is what keeps the GPU host's production
deployment untouched by this mechanism.

`host.env` is data, never code. `lib.sh` SHALL parse it rather than source it, so no
content of the file can execute, and SHALL accept only `KEY=VALUE` lines for an
explicit allowlist — `DEPLOYMENT`, `CONFIG`, `GPU_IDS` — plus comments and blank
lines; leading and trailing whitespace and a trailing carriage return SHALL be
stripped from each line before it is classified, so an indented comment or a
CRLF-edited file neither aborts nor poisons a value. Any other line SHALL abort
every consumer of `lib.sh` — `status.sh` and `nuke.sh` included — naming the
offending line: an unparsable `host.env` makes the deployment identity ambiguous,
and a teardown on an ambiguous identity is how the wrong deployment dies. A typo
must fail loudly and closed, not silently deploy (or destroy) the wrong identity. The
config pin (`CONFIG_REF`, `CONFIG_SHA`, `CONFIG_REPO`, `CONFIG_DIR`) and the secrets
path are deliberately outside the allowlist — they stay overridable per-invocation
only, never from a persistent git-excluded file, so the tracked-pin safety story
(bumped by PR, verified by `ensure_config`) is not weakened.

The effective precedence SHALL be: command-line environment, then `host.env`, then the
checked-in default — a `host.env` value applies only when the variable is not already
set, so an explicit command-line variable always wins. For the identity keys
(`DEPLOYMENT`, `CONFIG`) an empty environment value SHALL count as unset on both
sides: an empty name or path is never a valid identity, so an ambient
`DEPLOYMENT=''` can neither bypass the host pin nor fall through to the reserved
`dev`. For `GPU_IDS`, set-but-empty stays meaningful (the explicit disable), so
there set wins.

`GPU_IDS` SHALL keep the no-colon `${GPU_IDS-}` form, so a `host.env` line
`GPU_IDS=` (empty value) still means "explicitly no GPU flag" while an absent file
means "the default".

The name `dev` is reserved for the GPU host (`holygpu7c0717`), and the no-GPU /
no-local-vLLM workstation deploys as `claw` (issue #363, decision recorded
2026-08-25). The reserved names live in `host.env.example` and the scripts README, not
in code — the mechanism itself is name-agnostic.

#### Scenario: No host.env — the reserved default is preserved

- **WHEN** `lib.sh` is sourced on a host with no `deploy/fasrc-dev/scripts/host.env`
- **THEN** `DEPLOYMENT` resolves to `dev` and `CONFIG` to `deploy/fasrc-dev/config.yaml`
- **AND** sourcing succeeds (the missing file is not an error)

#### Scenario: host.env renames the deployment

- **WHEN** `host.env` contains the line `DEPLOYMENT=claw` and a deploy runs
- **THEN** `archi create` receives `--name claw`

#### Scenario: The command line beats host.env

- **WHEN** `host.env` contains `DEPLOYMENT=claw` and the operator runs
  `DEPLOYMENT=other ./redeploy.sh`
- **THEN** `DEPLOYMENT` resolves to `other`

#### Scenario: An empty identity variable does not bypass the host pin

- **WHEN** `host.env` contains `DEPLOYMENT=claw` and the environment carries
  `DEPLOYMENT=''` (empty)
- **THEN** `DEPLOYMENT` resolves to `claw`, not to the reserved default `dev`

#### Scenario: A key outside the allowlist fails every wrapper, closed

- **WHEN** `host.env` contains `CONFIG_SHA=deadbeef` (or any key other than
  `DEPLOYMENT`, `CONFIG`, `GPU_IDS`) and any wrapper script runs
- **THEN** the script aborts before `archi` is invoked, naming the offending line

#### Scenario: Edge whitespace and CRLF are tolerated, not poisonous

- **WHEN** `host.env` contains `  DEPLOYMENT=claw  `, an indented comment, a
  whitespace-only line, or a `DEPLOYMENT=claw` line with a CRLF ending
- **THEN** the deployment name resolves to exactly `claw`, with no stray
  whitespace or carriage return in the value

#### Scenario: host.env content is never executed

- **WHEN** `host.env` contains a non-assignment line such as `rm -f <path>`
- **THEN** the deploy aborts and the named command is not executed

#### Scenario: Empty GPU_IDS through host.env still disables the flag

- **WHEN** `host.env` contains the line `GPU_IDS=` and a deploy runs
- **THEN** `archi_deploy` passes no `--gpu-ids` flag
