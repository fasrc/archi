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

- `lib.sh` reads an optional, git-excluded `scripts/host.env` immediately after
  `REPO_ROOT` is resolved and before the deployment-identity block. The file is **data,
  not code**: it is parsed, never sourced, only `KEY=VALUE` lines for the allowlist
  `DEPLOYMENT` / `CONFIG` / `GPU_IDS` (plus comments and blanks) are accepted, a value
  applies only when the variable is not already set (the command line always wins), and
  any other line aborts before the deploy touches anything. The two identity knobs become
  overridable with today's values as the defaults: `DEPLOYMENT="${DEPLOYMENT:-dev}"`,
  `CONFIG="${CONFIG:-deploy/fasrc-dev/config.yaml}"`. `GPU_IDS="${GPU_IDS-}"` keeps its
  no-colon form — unset and empty stay distinguishable.
- A new tracked `host.env.example` documents the data format, the allowlist, the
  precedence, and both real host shapes: the GPU host (`DEPLOYMENT=dev`) and the
  workstation (`DEPLOYMENT=claw`). `host.env` itself is already ignored
  (`.gitignore:79`); the example is not. Verified both ways with `git check-ignore` — no
  `.gitignore` change.
- A new self-test `test_host_env.sh`, modeled on `test_gpu_flag.sh` (fake `archi` on `PATH`,
  renders no compose, starts no container), pins nine behaviors: the defaults survive a
  missing `host.env`; a missing `host.env` is not an error; a `host.env` `DEPLOYMENT`
  reaches `archi create --name`; `CONFIG` is honored; a command-line environment variable
  beats `host.env`; `GPU_IDS=` (empty) still disables the flag; a key outside the
  allowlist aborts the deploy; a non-assignment line aborts **and is never executed**
  (canary-checked); comments and blank lines parse.
- The false premise from `eff2ed6a` is corrected in `lib.sh:21-29` and
  `test_gpu_flag.sh:8-13` **without changing any default**: the real reason the default is
  off is that both containers are configured `device: cpu`, the models are served by a
  remote vLLM endpoint, and on the GPU host the GPUs are owned by vLLM. After the change,
  `grep -rn 'neither the nvidia container runtime' deploy/fasrc-dev/scripts/` returns
  nothing.
- `README.md` gains a "Per-host configuration" section: what `host.env` is for, the
  precedence order, and the reserved names.

## The decision this change had to make

How `host.env` is read. The issue sketched `[ -f host.env ] && . host.env` plus a
documented `: "${VAR:=value}"` idiom. The first adversarial-review round refuted that
design on two grounds, both verified against the code: sourcing executes arbitrary
shell from a git-excluded file before `require_files` and before `nuke.sh`'s
confirmation prompt, on the production host; and because every later knob uses
`${VAR:-default}`, a sourced `host.env` could silently override the config pin
(`CONFIG_REF`/`CONFIG_SHA`) that the tracked file exists to protect. The `:=` idiom
also carried a footgun: a plain assignment silently beat the command line.

The delivered design parses `host.env` as data instead: only `KEY=VALUE` lines for
`DEPLOYMENT`, `CONFIG`, `GPU_IDS` (plus comments and blanks) are accepted; a value
applies only when the variable is not already set, so the command line always wins
with no idiom to remember; any other line aborts before the deploy touches anything;
and nothing in the file can execute. Every acceptance criterion in issue #363 still
holds — the precedence criterion (`command-line env > host.env > checked-in default`)
holds unconditionally now, where the sourcing sketch only satisfied it for authors who
used the idiom.

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
