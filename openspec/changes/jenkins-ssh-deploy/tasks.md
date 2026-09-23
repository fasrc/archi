## 1. Deploy lock in `archi_deploy()` (test first)

- [ ] 1.1 Add lock cases to a new `deploy/scripts/test_remote_deploy.sh` (fixture tree + fake `archi`, the same harness style as `test_host_env.sh`): a held `archi-<name>.deploy.lock` makes `redeploy.sh` and `create.sh` exit non-zero, name the lock file, and never call `archi`; a lock for another deployment name does not block. Run it and watch it fail.
- [ ] 1.2 In `archi_deploy()` (`deploy/scripts/lib.sh`), take `flock -n` on `${ARCHI_DIR:-$HOME/.archi}/archi-$DEPLOYMENT.deploy.lock` before `ensure_config`; `mkdir -p` the parent; die naming the file on contention. Make 1.1 pass.
- [ ] 1.3 Run `test_host_env.sh`, `test_ensure_config.sh`, `test_gpu_flag.sh` and `test_firewall.sh` to confirm no regression.

## 2. Request allowlist and ref validation (test first)

- [ ] 2.1 Add cases: empty or unset `SSH_ORIGINAL_COMMAND`, `nuke`, `create`, `deploy`, `deploy a b`, `deploy dev;touch X`, `deploy $(id)`, `deploy --upload-pack=x`, `deploy dev/../../x` all exit 2, create no file, and run no git or fake-`archi` call; `status` runs `status.sh`. Watch them fail.
- [ ] 2.2 Create `deploy/scripts/remote-deploy.sh`: the whole body in `main`, last line `main "$@"; exit $?`; resolve the checkout from the script path; parse the request with `read -ra` (no `eval`); enforce the ref regex and the `..` ban. Make 2.1 pass.
- [ ] 2.3 Add cases on a fixture `origin`: tag beats a same-name branch; `origin/<b>` resolves; a local-only branch exits 2 naming it; a full 40-hex id present after fetch resolves; a short id is refused. Implement resolution after `git fetch --prune --tags origin`.

## 3. Checkout safety, run lock, deploy flow (test first)

- [ ] 3.1 Add cases: a tracked edit in the checkout exits 4 naming the file and keeps the edit; `host.env` and the `CONFIG` file survive a checkout; a held `.git/remote-deploy.lock` exits 3 before any fetch; a missing `archi` on `PATH` fails with a clear message.
- [ ] 3.2 Implement the tracked-edit check (`git status --porcelain --untracked-files=no`), `git checkout --detach <sha>`, the whole-run `flock -n` on `.git/remote-deploy.lock`, and the `command -v archi` check.
- [ ] 3.3 Add a case where the target commit ships a different `remote-deploy.sh`; the run in progress keeps its own logic (for example, the new file prints a marker that must not appear).
- [ ] 3.4 Add cases with a fake `redeploy.sh` and a fake `curl`: redeploy failure → exit 4, no smoke call, no `DEPLOYED`; chat HTTP 500 → exit 5, no `DEPLOYED`; success → last line `DEPLOYED deployment=<name> ref=<ref> commit=<sha>`, exit 0.
- [ ] 3.5 Implement the flow: `redeploy.sh` → `status.sh` → one chat turn (`config_name` from the `name:` key of `CONFIG`, epoch-ms timestamps, `curl -m 200`) → the provenance line. Make 3.3 and 3.4 pass.

## 4. Documentation and review

- [ ] 4.1 Add a "Remote deploys from Jenkins" section to `deploy/scripts/README.md`: manual trigger only (with the #294 and #372 warning), the deploy checkout setup, the two `authorized_keys` lines from design D3, the ProxyJump command, the exit-code table, and a sample Jenkins pipeline stage (45-minute timeout, no concurrent builds, credentials by id).
- [ ] 4.2 Add the new script and test to the script table in the README, and to `test_remote_deploy.sh`'s header list of cases.
- [ ] 4.3 Run every `deploy/scripts/test_*.sh`, then `bash scripts/gate.sh`, then `/codex:adversarial-review`, and address the findings before the PR.

## 5. Host rollout (operator, after merge)

- [ ] 5.1 `claw`: clone `~/deploy/archi`, copy `deploy/scripts/host.env` (its `CONFIG` lives in `config/`, which the deploy provisions), run `redeploy.sh` once by hand from the new checkout, add both key lines, then deploy `dev` from Jenkins and confirm the `DEPLOYED` line and a chat turn in the UI.
- [ ] 5.2 Confirm the FASRC key policy for an unattended key on `holygpu7c0717` (design Open Question 1). If refused, record that on the PR and stop; `claw` alone is the delivered scope.
- [ ] 5.3 GPU host, only if 5.2 permits: clone `~/deploy/archi`, copy `deploy/fasrc-dev/config.yaml`, run `redeploy.sh` once by hand from the new checkout, add the deploy key line, then deploy from Jenkins through the jump key.
