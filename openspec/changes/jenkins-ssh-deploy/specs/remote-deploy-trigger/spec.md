## ADDED Requirements

### Requirement: The remote entry point accepts only an allowlisted request
`deploy/scripts/remote-deploy.sh` SHALL read its request only from `SSH_ORIGINAL_COMMAND`, split it on whitespace without passing it to a shell, and accept exactly two forms: `status` and `deploy <ref>`.
An empty request, an unknown verb, a missing or extra argument, or any other form SHALL print usage on stderr and exit with code 2 before the script fetches, checks out, locks, or starts any container.

#### Scenario: Status request
- **WHEN** `SSH_ORIGINAL_COMMAND` is `status`
- **THEN** the script runs `status.sh` from its own checkout and exits with its exit code

#### Scenario: Interactive login is refused
- **WHEN** `SSH_ORIGINAL_COMMAND` is unset or empty
- **THEN** the script prints usage and exits 2, and no git or docker command runs

#### Scenario: Unknown verb is refused
- **WHEN** `SSH_ORIGINAL_COMMAND` is `nuke` or `create` or `deploy dev extra`
- **THEN** the script exits 2, and no git or docker command runs

#### Scenario: Shell syntax is not executed
- **WHEN** `SSH_ORIGINAL_COMMAND` is `deploy dev;touch /tmp/pwned` or `deploy $(id)`
- **THEN** the script exits 2 and no file is created

### Requirement: The deploy ref is validated and resolved only against origin
For `deploy <ref>`, the script SHALL reject a ref that does not match `^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$` or that contains `..`, with exit code 2 and before any fetch.
After `git fetch --prune --tags origin`, the script SHALL resolve the ref as a tag `refs/tags/<ref>`, else a remote branch `refs/remotes/origin/<ref>`, else a full 40-hex commit id present after the fetch, and SHALL NOT resolve a local branch name. An unresolvable ref SHALL exit 2 and name the ref. The resolved commit id SHALL be printed before the checkout.

#### Scenario: Remote branch resolves
- **WHEN** the request is `deploy dev` and `origin/dev` exists
- **THEN** the checkout is detached at `origin/dev`'s commit

#### Scenario: Tag has priority over a branch of the same name
- **WHEN** a tag and a remote branch are both named `v2026.9.0`
- **THEN** the tag's commit is deployed

#### Scenario: A stale local branch is ignored
- **WHEN** a local branch `feature` exists but `origin/feature` does not
- **THEN** the script exits 2 and names `feature` as unresolvable

#### Scenario: Leading dash or path traversal is rejected
- **WHEN** the ref is `--upload-pack=x` or `dev/../../x`
- **THEN** the script exits 2 before `git fetch` runs

### Requirement: The deploy checkout is never destroyed or mixed with local edits
The script SHALL operate only on the checkout that contains it, resolved from its own path, and SHALL abort with exit code 4, naming the paths, if that checkout has tracked modifications.
The script SHALL NOT run `git reset --hard`, `git clean`, or `git stash`, and SHALL move to the resolved commit only with `git checkout --detach <commit>`. Untracked and ignored files (`host.env`, the `CONFIG` file, `config/`) SHALL stay in place.

#### Scenario: Tracked edit aborts
- **WHEN** a tracked file in the deploy checkout is modified
- **THEN** the script exits 4 before `git checkout`, names the file, and the file keeps its edit

#### Scenario: Host files survive the checkout
- **WHEN** a deploy moves the checkout to a new commit
- **THEN** `deploy/scripts/host.env` and the `CONFIG` file are unchanged after the run

### Requirement: Deploys of one deployment are serialized
`archi_deploy()` in `deploy/scripts/lib.sh` SHALL take a non-blocking exclusive `flock` on `${ARCHI_DIR:-$HOME/.archi}/archi-<DEPLOYMENT>.deploy.lock` before `ensure_config`, and SHALL hold it until the function returns.
If the lock is held, the deploy SHALL fail at once, before `ensure_config` and before `archi create`, and name the lock file. The lock file SHALL be outside the `archi-<DEPLOYMENT>` directory. `remote-deploy.sh` SHALL also hold a non-blocking lock on `<checkout>/.git/remote-deploy.lock` for its whole run, and exit 3 if that lock is held.

#### Scenario: Manual redeploy during a deploy
- **WHEN** one `redeploy.sh` for deployment `claw` holds the deployment lock and a second `redeploy.sh` for `claw` starts
- **THEN** the second exits non-zero, names the lock file, and `archi` is never invoked by it

#### Scenario: Different deployments do not block each other
- **WHEN** deployments `claw` and `ci` deploy at the same time on one host
- **THEN** neither one fails on the lock

#### Scenario: Two remote runs on one checkout
- **WHEN** a second `deploy <ref>` request arrives while the first still runs
- **THEN** the second exits 3 before `git fetch`

### Requirement: A remote deploy is verified and reports provenance
After a checkout, the script SHALL run the new commit's `redeploy.sh`, then `status.sh`, then one live chat turn (`POST /api/get_chat_response` to `CHAT_URL`, `config_name` from the `name:` key of `CONFIG`, epoch-millisecond timestamps, a 200-second timeout).
The script SHALL exit 4 if `redeploy.sh` fails and 5 if the chat turn does not return HTTP 200. On success it SHALL print, as its last line, `DEPLOYED deployment=<name> ref=<ref> commit=<sha>` and exit 0.

#### Scenario: Successful deploy
- **WHEN** `redeploy.sh` succeeds and the chat turn returns HTTP 200
- **THEN** the last line of output is `DEPLOYED deployment=<name> ref=<ref> commit=<sha>` and the exit code is 0

#### Scenario: Smoke test failure
- **WHEN** `redeploy.sh` succeeds and the chat turn returns HTTP 500
- **THEN** the exit code is 5 and no `DEPLOYED` line is printed

#### Scenario: Deploy failure
- **WHEN** `redeploy.sh` exits non-zero
- **THEN** the exit code is 4, and neither the smoke test nor the `DEPLOYED` line runs

### Requirement: The entry script survives replacement of its own file
The script body SHALL run from a single function that bash parses in full before `git checkout` can replace the script file.
A checkout that changes `remote-deploy.sh` SHALL NOT change the behavior of the run already in progress.

#### Scenario: The deployed commit changes the entry script
- **WHEN** the target commit contains a different `remote-deploy.sh`
- **THEN** the current run completes with the logic it started with, and the next run uses the new file
