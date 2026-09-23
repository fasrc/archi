## Why

Today a deploy runs only by hand: an operator (or a Claude session) opens a shell on the
target host and runs `deploy/scripts/redeploy.sh`. There is no one-click trigger, no
shared log of who deployed which commit, and no record that outlives the terminal.
A Jenkins server already runs on the `claw` workstation (the `jenkins/jenkins` LTS image,
in a Docker container outside this project). This change lets that Jenkins start a
deploy of either stack by a manual button, without a shell session on the host.

The deploy cannot move into the Jenkins container. `archi create --hostmode` needs the
host's own DNS, host-local secrets (`~/.secrets/archi-secrets.env`, `host.env`) and a
local image build. The container has no `docker`, no archi toolchain, and it cannot
resolve `holygpu7c0717.rc.fas.harvard.edu`. So Jenkins starts the deploy over SSH, and
the deploy still runs through `redeploy.sh` on the host, as the user `austin`.

## What Changes

- Add `deploy/scripts/remote-deploy.sh`: the one entry point that an SSH forced command
  (an `authorized_keys` rule that lets a key run only one program) calls. It reads the
  request from `SSH_ORIGINAL_COMMAND` and accepts only two verbs: `deploy <ref>` and
  `status`. Any other request is refused before it touches anything.
- `deploy <ref>` validates the ref, fetches `origin`, checks out the resolved commit
  (detached) in a **dedicated deploy checkout**, runs `redeploy.sh`, then `status.sh`,
  then a live chat smoke test. It prints a final provenance line with the deployment
  name, the ref, and the commit id.
- The entry script refuses to run in a checkout with tracked edits, and never uses
  `git reset --hard` or `git clean`.
- Add a deploy lock to `archi_deploy` in `deploy/scripts/lib.sh`: a Jenkins deploy and a
  manual `create.sh` or `redeploy.sh` on the same deployment cannot run at the same time.
  A second deploy fails at once and names the lock file.
- Add `deploy/scripts/test_remote_deploy.sh`: a self-test against a fixture repository and
  a fake `archi`, in the style of `test_host_env.sh`.
- Document the host setup in `deploy/scripts/README.md`: the deploy checkout, the two
  SSH keys and their `authorized_keys` lines, the ProxyJump route to the GPU host, and a
  sample Jenkins pipeline stage.
- Trigger policy: **manual only**. No push, merge or schedule trigger. Automatic deploys
  wait for #294 and #372 (a `--force` redeploy tears down before it renders, and deletes
  the evaluation catalog).

Not in this change: the Jenkinsfile, the Jenkins credentials, and the Jenkins job
itself. Those live in the Jenkins setup, outside this repository. `create.sh` and
`nuke.sh` stay manual-only; the SSH keys cannot reach them.

## Capabilities

### New Capabilities
- `remote-deploy-trigger`: the SSH forced-command entry point for a manually triggered
  remote deploy — request allowlist, ref validation, dedicated-checkout safety, the
  deploy lock, the post-deploy checks, and the provenance output.

### Modified Capabilities
<!-- none: deploy-config-provisioning requirements are unchanged; ensure_config still
     runs inside redeploy.sh exactly as today -->

## Impact

- **Code:** new `deploy/scripts/remote-deploy.sh` and `deploy/scripts/test_remote_deploy.sh`;
  a lock added to `archi_deploy()` in `deploy/scripts/lib.sh` (so `create.sh` and
  `redeploy.sh` also take the lock); README section. No Python and no application code,
  so the `diff-cover` gate is not affected.
- **Hosts:** `claw` gets a deploy checkout (`~/deploy/archi`) and two `authorized_keys`
  lines. The GPU host gets a deploy checkout and one `authorized_keys` line, after the
  FASRC key-policy check (see design Open Questions).
- **Release plan:** operator tooling, not in a milestone and not gating a release.
  It lowers the cost of the operator-driven `needs-deploy` items (for example #340).
- **Security:** each key can run only `remote-deploy.sh` (or, for the jump key, open one
  TCP route). A holder of the deploy key can deploy any commit that is on `origin`, which
  is the same power as a manual deploy today.
