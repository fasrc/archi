## Why

PR #644 gave each deployment its own services list, and `dev` now runs the Slack bot. The
deploy README did not change with it. Codex posted this finding on #644 three minutes after
the merge, and no person answered the thread (issue #714; thread `r4233054823` on PR #644).
Measured on `origin/dev` `82850b3b`:

| Fact | Where | State |
|---|---|---|
| `dev` runs `SERVICES="chatbot,slack"`; every other name runs `SERVICES="chatbot"` | `deploy/scripts/lib.sh`, the `SERVICES` case block | in code |
| `SERVICES` goes to `archi create --services` | `deploy/scripts/lib.sh` (`archi_deploy`) | in code |
| The `slack` service needs `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` | `src/cli/service_registry.py:180` (`required_secrets`) | in code |
| `archi create` stops when a required secret is not in the env file | `src/cli/managers/secrets_manager.py:164-182` (`validate_secrets`) | in code |
| The secrets bullet names `PG_PASSWORD`, `HUIT_API_KEY`, `ANTHROPIC_API_KEY` only | `deploy/scripts/README.md:197-199` | no Slack key |
| The README describes the services list of each deployment | `deploy/scripts/README.md` | absent (`grep -n SERVICES` prints nothing) |

Result: an operator who rebuilds the dev host from the README gets an `archi create`
refusal for 2 secrets that the README does not name.

## What Changes

- The "Notes" list of `deploy/scripts/README.md` gets one new "Services" bullet. It says
  that the `SERVICES` case block in `lib.sh` sets the services list of each deployment,
  that `dev` runs `chatbot,slack` and every other name runs `chatbot`, and that the list is
  not read from the environment or `host.env`.
- The "Secrets" bullet of the same file names `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` as
  required for the `dev` deployment only, because `dev` runs the Slack service.
- No code, test, or config change. The README names the keys only, never a value, and
  names the `SERVICES` variable, never a `lib.sh` line number.

## Capabilities

### New Capabilities

- `deploy-services`: the deploy README tells an operator which services each deployment
  runs and which secrets those services need.

### Modified Capabilities

None.

## Impact

- Docs: `deploy/scripts/README.md` (one new bullet, one changed bullet).
- Code, tests, config: none. The issue limits the change to the README. Apart from this
  change directory, `git diff --name-only origin/dev` prints only `deploy/scripts/README.md`
  (design D3).
- Runtime: none. The running dev host already has both tokens. The README is not part of
  the docs site (`docs/mkdocs.yml` does not include it), so no docs build is affected.
- Out of the loop: the issue asks for a reply on the #644 thread that names the fix
  commit. That reply is a GitHub write with no commit, so it is not a task here. The
  nightly wrap-up or the reviewer does it after the PR opens.
