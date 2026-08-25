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
lines. Any other line SHALL abort before the deploy touches anything, naming the
offending line: a typo must fail loudly, not silently deploy the wrong identity. The
config pin (`CONFIG_REF`, `CONFIG_SHA`, `CONFIG_REPO`, `CONFIG_DIR`) and the secrets
path are deliberately outside the allowlist — they stay overridable per-invocation
only, never from a persistent git-excluded file, so the tracked-pin safety story
(bumped by PR, verified by `ensure_config`) is not weakened.

The effective precedence SHALL be: command-line environment, then `host.env`, then the
checked-in default — a `host.env` value applies only when the variable is not already
set, so an explicit command-line variable always wins.

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

#### Scenario: A key outside the allowlist aborts the deploy

- **WHEN** `host.env` contains `CONFIG_SHA=deadbeef` (or any key other than
  `DEPLOYMENT`, `CONFIG`, `GPU_IDS`) and a deploy runs
- **THEN** the deploy aborts before touching anything, naming the offending line

#### Scenario: host.env content is never executed

- **WHEN** `host.env` contains a non-assignment line such as `rm -f <path>`
- **THEN** the deploy aborts and the named command is not executed

#### Scenario: Empty GPU_IDS through host.env still disables the flag

- **WHEN** `host.env` contains the line `GPU_IDS=` and a deploy runs
- **THEN** `archi_deploy` passes no `--gpu-ids` flag
