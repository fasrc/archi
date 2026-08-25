# Make the dev deploy scripts host-aware via an optional host.env

## Why

Two hosts deploy archi from this repository: the GPU host (`holygpu7c0717.rc.fas.harvard.edu`,
the only production deployment, name `dev`) and a no-GPU developer workstation. The two knobs
that define deployment *identity* are hardcoded in the shared, tracked
`deploy/fasrc-dev/scripts/lib.sh` — verified on `origin/dev` at `9ff794de`:

```bash
15:DEPLOYMENT="dev"
16:CONFIG="deploy/fasrc-dev/config.yaml"
```

while six other knobs honor an environment override (`lib.sh:19,30,44-47`). Both hosts
therefore render the identical identity — containers `chatbot-dev` / `data-manager-dev` /
`postgres-dev`, volumes `archi-dev` / `archi-pg-dev` / `archi-data-dev` — and the workstation
cannot take its own name without an uncommitted local edit to a tracked file.

The cost of sharing one host's values is measured, not hypothetical. Commit `eff2ed6a`
(2026-08-17) justified a shared `GPU_IDS` default with "the host has neither the nvidia
container runtime nor nvidia-smi" (`lib.sh:23-24`), a claim true of the workstation and
provably false of the GPU host — and the change silently rebuilt the GPU host's data-manager
image from the CUDA variant to the CPU variant on its next deploy (18.9GB -> 4.59GB). The
same false premise is repeated at `test_gpu_flag.sh:8-13`.

Issue #363 carries the work order, and its 2026-08-25 naming decision: `dev` stays reserved
for the GPU host; the no-GPU / no-local-vLLM workstation deploys as `claw`.

## What Changes

- `lib.sh` sources an optional, git-excluded `scripts/host.env` immediately after
  `REPO_ROOT` is resolved and before the deployment-identity block, and the two identity
  knobs become overridable with today's values as the defaults:
  `DEPLOYMENT="${DEPLOYMENT:-dev}"`, `CONFIG="${CONFIG:-deploy/fasrc-dev/config.yaml}"`.
  `GPU_IDS="${GPU_IDS-}"` keeps its no-colon form — unset and empty stay distinguishable.
- A new tracked `host.env.example` documents the `: "${VAR:=value}"` idiom and both real
  host shapes: the GPU host (`DEPLOYMENT=dev`) and the workstation (`DEPLOYMENT=claw`).
  `host.env` itself is already ignored (`.gitignore:79`); the example is not. Verified both
  ways with `git check-ignore` — no `.gitignore` change.
- A new self-test `test_host_env.sh`, modeled on `test_gpu_flag.sh` (fake `archi` on `PATH`,
  renders no compose, starts no container), pins seven behaviors: the defaults survive a
  missing `host.env`; a `host.env` `DEPLOYMENT` reaches `archi create --name`; `CONFIG` is
  honored; a command-line environment variable beats a `:=`-style `host.env`; a **plain**
  assignment in `host.env` beats the command line (the documented tradeoff, pinned so it is
  executable rather than folklore); `GPU_IDS=""` still disables the flag; a missing
  `host.env` is not an error.
- The false premise from `eff2ed6a` is corrected in `lib.sh:21-29` and
  `test_gpu_flag.sh:8-13` **without changing any default**: the real reason the default is
  off is that both containers are configured `device: cpu`, the models are served by a
  remote vLLM endpoint, and on the GPU host the GPUs are owned by vLLM. After the change,
  `grep -rn 'neither the nvidia container runtime' deploy/fasrc-dev/scripts/` returns
  nothing.
- `README.md` gains a "Per-host configuration" section: what `host.env` is for, the
  precedence order, and the reserved names.

## The decision this change had to make

Precedence. "Command line beats `host.env`" holds only when the `host.env` author uses
`: "${VAR:=value}"`. A plain `DEPLOYMENT=claw` assignment wins over the command line,
because the file is sourced before the defaults are applied. Enforcing the idiom in
`lib.sh` — snapshotting the pre-source environment and re-applying it after — buys
strictness at the cost of new machinery in a file every deploy sources with `set -euo
pipefail`, and the failure it would prevent is local to the host that wrote the file and
visible on the next `status.sh`. This change takes the issue's route: document the idiom in
`host.env.example` and the README, and pin **both** behaviors in the self-test — the `:=`
form yielding to the command line, and the plain form beating it — so the tradeoff is
recorded as a passing test, not a footnote.

## Capabilities

### New Capabilities

- `deploy-host-identity`: how a host pins its deployment identity without editing tracked
  files. The directory does not exist under `openspec/specs/`; the nearest capability,
  `deploy-config-provisioning`, is scoped to the `config/` checkout and does not cover
  identity.

### Modified Capabilities

None.

## Impact

- `deploy/fasrc-dev/scripts/lib.sh` — the sourcing block and the two identity defaults.
  Shared by `create.sh` / `redeploy.sh` / `nuke.sh` / `status.sh` on the live production
  host: the self-tests are the guard, because nothing here reports to `diff-cover`
  (`scripts/gate.sh` measures `--cov=src`) and the shell self-tests are not wired into CI.
- `deploy/fasrc-dev/scripts/test_host_env.sh` — new.
- `deploy/fasrc-dev/scripts/host.env.example` — new, tracked.
- `deploy/fasrc-dev/scripts/test_gpu_flag.sh` — comment block only; the `${GPU_IDS-}`
  contract it pins is untouched.
- `deploy/fasrc-dev/scripts/README.md` — documentation.
- No deploy runs as part of this change. Renaming the workstation deployment to `claw`
  happens after merge, by writing `host.env` on that host and redeploying — fresh volumes,
  a local corpus re-ingest (~50 min), and stopping the old `dev`-named containers first.
  The GPU host is untouched: with no `host.env` it resolves exactly today's values.
