## Context

Two deployments run from this repository (issue #363):

| Deployment | Host | Reach from the Jenkins container |
|---|---|---|
| `claw` | the workstation that also runs Jenkins | `172.22.0.1:22` (gateway of the `media-composarr_proxy` network) — open |
| `dev` | `holygpu7c0717.rc.fas.harvard.edu` (`10.31.143.106`) | port 22 open by IP; the **name does not resolve** in the container |

Jenkins is the stock `jenkins/jenkins:2.568.3-lts-jdk21` container. Its only mount is
`/home/austin/data/jenkins/home`. It has `ssh` and `git`, and no `docker` and no archi
toolchain. It is outside this project and stays that way.

`redeploy.sh` → `archi_deploy()` (`deploy/scripts/lib.sh`) needs, on the host:
`--hostmode` DNS, `~/.secrets/archi-secrets.env`, `deploy/scripts/host.env`, the git-excluded
`CONFIG` file, the private `config/` checkout (provisioned by `ensure_config`), the
miniforge `archi` env on `PATH`, and the local Docker daemon. None of these are in
the Jenkins container. So the deploy runs on the host, and Jenkins only starts it.

The trigger is **manual only**. The deploy runs as the user **`austin`** on both hosts.

## Goals / Non-Goals

**Goals:**
- One Jenkins button per deployment: pick a ref, deploy, see the full log and a clear
  pass or fail.
- `redeploy.sh` stays the one deploy path. The remote entry adds no second way to run
  `archi create`.
- The SSH keys that Jenkins holds can do nothing except start this entry point.
- A Jenkins deploy and a manual deploy on the same deployment cannot overlap.
- A Jenkins deploy never uses or changes the operator's working copy `~/Projects/archi`.

**Non-Goals:**
- Automatic triggers (push, merge, schedule). Blocked on #294 and #372.
- `create.sh` (first stand-up) and `nuke.sh` over SSH.
- The Jenkinsfile, the Jenkins credentials, and the job configuration. The README gives a
  sample stage only.
- A separate service user. The deploy runs as `austin`.
- Any change to the Jenkins container (no Docker socket, no extra tools).

## Decisions

### D1. SSH from the container to the host, not a Docker socket mount

```
 ┌──────────────────── claw ─────────────────────────────────────┐
 │ ┌─────────┐ deploy key    ┌──────────────────────────────────┐ │
 │ │ jenkins │──────────────►│ sshd → remote-deploy.sh (claw)   │ │
 │ │container│ 172.22.0.1    └──────────────────────────────────┘ │
 │ └────┬────┘ jump key: open holygpu7c0717:22 only              │
 └──────┼─────────────────────────────┬──────────────────────────┘
        └──────── ssh -J ─────────────▼
                    holygpu7c0717: sshd → remote-deploy.sh (dev)
```

*Alternatives:* mount `/var/run/docker.sock` into Jenkins, or run a Jenkins agent (a
worker program that Jenkins controls) on each host. The socket gives Jenkins control of
every container on `claw` (pihole, plex, syncthing, …). Compose bind-mount paths
resolve on the host, not in the container. The container also still has no archi CLI,
no secrets and no host DNS. An agent needs a Java service on each host, and a GPU-node
login rule can forbid it. SSH needs nothing new in the container, and it runs the
deploy in the same environment as a manual deploy.

### D2. The GPU host leg uses ProxyJump through `claw`

The container cannot resolve the GPU host name, because that name resolves only through
the host's split DNS. With `ssh -J`, `claw` does the name lookup and the VPN routing.
*Alternative:* hard-code `10.31.143.106` in the Jenkins `extra_hosts`. That breaks in
silence when the node gets a new address. It is the same failure that the deploy-verify
guidance warns about for the vLLM endpoint.

### D3. Two keys, each limited in `authorized_keys`

sshd applies the **first** `authorized_keys` line that matches a key. So one key cannot
be a forced-command deploy key and a jump key on the same host. Jenkins holds two keys:

| Key | Host | `authorized_keys` options |
|---|---|---|
| deploy | `claw` and GPU host | `restrict,command="<env PATH=…> <checkout>/deploy/scripts/remote-deploy.sh"` |
| jump | `claw` only | `restrict,port-forwarding,permitopen="holygpu7c0717.rc.fas.harvard.edu:22",command="/bin/false"` |

`restrict` turns off PTY, agent and X11 forwarding, and all port forwarding except the
explicit `permitopen` target. A ProxyJump hop opens a forwarding channel and no session,
so `command="/bin/false"` blocks a shell and the hop still works.

### D4. `PATH` comes from the forced command, not from a tracked file

The miniforge path (`/home/austin/miniforge3/envs/archi/bin`) is host-specific. The
`authorized_keys` line sets it with `env PATH=… remote-deploy.sh`. *Alternative:* add a
`PATH` key to `host.env`. That widens the strict `host.env` allowlist, which is a
security contract (`test_host_env.sh`), and a `PATH` value there can run any program.
The entry script checks at start that `archi` is on `PATH`, and fails with a clear
message if not.

### D5. A dedicated deploy checkout, detached at the requested commit

Each host gets `~/deploy/archi`, a plain clone of `fasrc/archi`, with its own `host.env`,
`CONFIG` file and `config/` checkout. `remote-deploy.sh` runs `redeploy.sh` from
**its own** checkout (it resolves the repo root from its own path, the same as `lib.sh`).

- *Why not `~/Projects/archi`?* On `claw` that is the operator's live working copy, with
  worktrees and untracked work. A `git checkout <ref>` there mixes in work in progress,
  or aborts on it.
- Both checkouts render into the same `~/.archi/archi-<name>`. This is correct: they
  deploy the same deployment. The lock (D7) covers both.
- Tracked edits in the deploy checkout abort the deploy (the checkout is machine-owned;
  edits there are a mistake). Untracked and ignored files (`host.env`, `CONFIG`,
  `config/`) stay in place. `git reset --hard` and `git clean` are never used, the same
  rule as `ensure_config`.

### D6. Request grammar and ref validation

`SSH_ORIGINAL_COMMAND` is split on spaces, and never passed to a shell:

- `status` → `status.sh`.
- `deploy <ref>` → the deploy flow.
- empty (an interactive login), or anything else → usage on stderr, exit 2.

`<ref>` must match `^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$` and must not contain `..`.
After `git fetch --prune --tags origin`, it resolves in this order: a tag
`refs/tags/<ref>`, a remote branch `refs/remotes/origin/<ref>`, or a full 40-hex commit
id that exists locally after the fetch. Local branch names are never used, because they
can be stale. The resolved commit id is printed before the checkout.

*Alternative:* accept only tags. That is too strict for a dev stack, where the usual
target is `dev` or a PR branch. The power is the same as a manual deploy: anyone who
holds the key can deploy any commit on `origin`.

### D7. The lock lives in `archi_deploy()`, not only in the entry script

A lock only in `remote-deploy.sh` does not stop a manual `redeploy.sh` from overlapping a
Jenkins deploy. So `archi_deploy()` takes `flock -n` on
`${ARCHI_DIR:-$HOME/.archi}/archi-<name>.deploy.lock` before `ensure_config`, and holds it
until the function returns. The lock file sits next to (not inside) the deployment
directory, because `archi create --force` deletes that directory. A second deploy fails at
once and names the lock file. It does not wait: a queued deploy of an old ref after a
newer one is worse than a clear failure.

`remote-deploy.sh` also holds a second, separate lock for the whole run:
`flock -n` on `<checkout>/.git/remote-deploy.lock`. That lock guards the deploy
checkout, so two Jenkins runs cannot change it at the same time. The two locks have
different jobs, so neither one needs a bypass:

| Lock | Held by | Guards | A second run |
|---|---|---|---|
| `<checkout>/.git/remote-deploy.lock` | `remote-deploy.sh`, whole run | the deploy checkout | fails before `git fetch` (exit 3) |
| `~/.archi/archi-<name>.deploy.lock` | `archi_deploy()` | the deployment | fails before `ensure_config` |

If a manual deploy runs when Jenkins starts, the Jenkins run checks out its ref and then
fails at the deployment lock. That is safe: the deploy checkout moved, and the running
deployment did not. The Jenkins job also sets "do not allow concurrent builds".

### D8. The entry script is safe against its own checkout

`git checkout` replaces `remote-deploy.sh` while bash still runs it, and bash reads a
script file as it goes. The whole script body is in a `main` function, with
`main "$@"; exit $?` as the last line, so bash parses all of it before the checkout
occurs. After the checkout, the script runs the **new** commit's `redeploy.sh`,
`status.sh` and smoke test.

### D9. Post-deploy checks and output

After `redeploy.sh` succeeds, the script runs `status.sh`, then a smoke test: one
`POST /api/get_chat_response` turn to `CHAT_URL`, with `config_name` read from the
`name:` key of `CONFIG`, epoch-millisecond timestamps and `curl -m 200` (a first turn
takes about 40 seconds). A non-200 response fails the deploy. The last line of a pass is
`DEPLOYED deployment=<name> ref=<ref> commit=<sha>`. Exit codes: 0 pass, 2 bad request,
3 lock held, 4 deploy failed, 5 smoke test failed. Jenkins shows the log and the exit code.

## Risks / Trade-offs

- [The deploy key can deploy any commit on `origin`, and `dev` has no branch protection]
  → The same power as a manual deploy today. The trigger is manual, and the job is
  visible only in Jenkins on `claw`. Keep the Jenkins UI on localhost or the tailnet.
- [A deploy of a ref also changes the next run's `remote-deploy.sh`] → Accepted: that
  is what "deploy this commit" means. A bad entry script is fixed by a manual deploy
  from the operator checkout, which does not use the entry script.
- [`--force` still tears down before it renders (#294), and deletes the evaluation
  catalog (#372)] → The trigger is manual only. The README repeats this warning next to
  the Jenkins stage.
- [The Jenkins SSH private keys sit in `/home/austin/data/jenkins/home`] → Store them as
  Jenkins credentials, not as plain files. Each key can run only the entry script, or
  open one TCP route. Revoke by deleting one `authorized_keys` line.
- [A Jenkins timeout kills `ssh` in the middle of `archi create`] → sshd sends SIGHUP to
  the forced command. The job timeout must be longer than a normal deploy (set 45 minutes
  in the sample stage). A half-done deploy is the same state as an interrupted manual one;
  recover with `redeploy.sh`.
- [The shell self-tests do not run in CI or the gate] → The same as `test_host_env.sh`
  and `test_ensure_config.sh` today. The tasks run the test by hand before each commit.

## Migration Plan

1. Merge this change. Nothing runs until a host adds an `authorized_keys` line.
2. `claw`: clone `~/deploy/archi`, copy `host.env`, run `redeploy.sh` once by hand from
   it (this also provisions `config/`, where claw's `CONFIG` lives), add both key lines, and deploy from
   Jenkins.
3. GPU host: only after the policy check (Open Question 1). Then the same steps with the
   deploy key only.
4. Rollback: delete the `authorized_keys` lines. Manual deploys from `~/Projects/archi`
   keep working; the only change they see is the lock.

## Open Questions

1. **FASRC key policy on `holygpu7c0717`.** Do FASRC rules permit an unattended SSH key
   for `austin` on this node (no OTP)? If not, the GPU host leg is dropped, and only
   `claw` gets the Jenkins trigger. Task group 5 waits on this answer.
2. Does the `dev` deploy today run from `~/Projects/archi` on the GPU host, and where is its
   `CONFIG` file? The deploy checkout needs a copy of that file.
