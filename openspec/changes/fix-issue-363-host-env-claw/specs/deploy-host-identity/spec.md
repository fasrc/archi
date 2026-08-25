## ADDED Requirements

### Requirement: A host pins its deployment identity without editing tracked files

`deploy/fasrc-dev/scripts/lib.sh` SHALL source an optional, git-excluded
`deploy/fasrc-dev/scripts/host.env` immediately after `REPO_ROOT` is resolved and before
the deployment-identity defaults are applied, and SHALL honor overrides of `DEPLOYMENT`
and `CONFIG`, with the checked-in defaults `dev` and `deploy/fasrc-dev/config.yaml`. A
missing `host.env` SHALL NOT be an error: a host that writes no file resolves exactly the
values the tracked file ships, which is what keeps the GPU host's production deployment
untouched by this mechanism.

The effective precedence SHALL be: command-line environment, then a `host.env` written
with `: "${VAR:=value}"`, then the checked-in default. A plain `VAR=value` assignment in
`host.env` beats the command-line environment — that is a property of sourcing before the
defaults, it is the documented tradeoff of this design, and the self-test pins it so the
behavior is recorded executably rather than discovered in an outage.

`GPU_IDS` SHALL keep the no-colon `${GPU_IDS-}` form, so a `host.env` (or the command
line) can distinguish "unset — use the default" from "empty — explicitly no GPU flag".

The name `dev` is reserved for the GPU host (`holygpu7c0717`), and the no-GPU /
no-local-vLLM workstation deploys as `claw` (issue #363, decision recorded 2026-08-25).
The reserved names live in `host.env.example` and the scripts README, not in code — the
mechanism itself is name-agnostic.

#### Scenario: No host.env — the reserved default is preserved

- **WHEN** `lib.sh` is sourced on a host with no `deploy/fasrc-dev/scripts/host.env`
- **THEN** `DEPLOYMENT` resolves to `dev` and `CONFIG` to `deploy/fasrc-dev/config.yaml`
- **AND** sourcing succeeds (the missing file is not an error)

#### Scenario: host.env renames the deployment

- **WHEN** `host.env` contains `: "${DEPLOYMENT:=claw}"` and a deploy runs
- **THEN** `archi create` receives `--name claw`

#### Scenario: The command line beats host.env

- **WHEN** `host.env` contains `: "${DEPLOYMENT:=claw}"` and the operator runs
  `DEPLOYMENT=other ./redeploy.sh`
- **THEN** `DEPLOYMENT` resolves to `other`

#### Scenario: A plain assignment beats the command line — pinned, not preferred

- **WHEN** `host.env` contains the plain assignment `DEPLOYMENT=claw` and the operator
  runs `DEPLOYMENT=other ./redeploy.sh`
- **THEN** `DEPLOYMENT` resolves to `claw`, and `test_host_env.sh` records exactly this
  behavior so a future change to it is a deliberate, red-test-first decision

#### Scenario: Empty GPU_IDS through host.env still disables the flag

- **WHEN** `host.env` sets `GPU_IDS=""` and a deploy runs
- **THEN** `archi_deploy` passes no `--gpu-ids` flag
