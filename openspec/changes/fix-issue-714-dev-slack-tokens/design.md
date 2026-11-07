## Context

`deploy/scripts/lib.sh` sets `SERVICES` with a `case "$DEPLOYMENT"` block: `dev` gets
`chatbot,slack`, and every other name (`claw` too) gets `chatbot`. The block assigns the
value with no environment default, so a `SERVICES` value in the environment has no effect.
`tests/unit/test_deploy_services_per_deployment.py` pins all three facts. `archi_deploy`
passes the value as `archi create --services "$SERVICES"`.

`archi create` collects the required secrets of every service in that list
(`SecretsManager.get_required_secrets_for_services` reads each service's
`required_secrets` from the service registry) and `validate_secrets` raises
`Missing required secrets in <env file>` when one is absent. The `slack` service declares
`required_secrets=["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]`. A deployment that does not run
`slack` does not need the two keys.

`deploy/scripts/README.md` ends with a "## Notes" bulleted list. Its last bullet is the
"Secrets" bullet. The bullet just before it is the "Config" bullet.

## Goals / Non-Goals

**Goals**
- The README names `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` as required for `dev` only.
- The README says where the services list of each deployment lives and what each row runs.
- The four commands of the issue give the stated results (design D3).

**Non-Goals**
- `ARCHI_API_TOKEN`. The secrets manager also requires it for `slack` when chat auth is on.
  `dev` runs with chat auth off, and the issue does not ask for it. Not added.
- The `/v1` and `openai_compat.local_only` setup that the `lib.sh` comment also names. It
  lives in the deployment config, not in the secrets env, and the issue does not ask for it.
- A test that pins the README text to `lib.sh`. The issue limits the change to the README.
- Any change to `lib.sh`, to the env file, or to a deployment config.
- The reply on the #644 thread (a GitHub write with no commit; the wrap-up or the reviewer
  does it after the PR opens).

## Decisions

### D1. A new "Services" bullet in "## Notes"

Insert this bullet directly before the "Secrets" bullet (that is, after the "Config"
bullet), so that the Secrets bullet can point back to it:

```markdown
- Services: each deployment runs a fixed services list, set by the `SERVICES` case
  block in `lib.sh` and passed to `archi create --services`. The block has a `dev` row
  and a `*` row for every other name: today `dev` runs `chatbot,slack` and every other
  name runs `chatbot`. The list is not read from the environment or `host.env`, so a
  change to one host's services is a reviewed change to `lib.sh`. Pinned by
  `tests/unit/test_deploy_services_per_deployment.py`.
```

Name the variable and the file. Do not write a `lib.sh` line number (the issue's plan
step 3): line numbers go stale when `lib.sh` changes. Do not add claims about which
other containers a service brings up: the service registry is the source of truth for
that, and this bullet does not need it.

### D2. The Slack keys in the "Secrets" bullet

Replace the "Secrets" bullet with this text. The first three lines are the current text,
unchanged; the last three lines are new:

```markdown
- Secrets: an env file with `PG_PASSWORD` (required) plus `HUIT_API_KEY` /
  `ANTHROPIC_API_KEY` as needed. Defaults to `~/.secrets/archi-secrets.env`;
  override with the `ARCHI_ENV_FILE` env var. Resolved in `lib.sh` — never commit it.
  The `dev` deployment also needs `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` in this file,
  because it runs the Slack service (see "Services" above); `archi create` refuses to
  run when either key is missing. No other deployment needs them.
```

The new sentences go after "never commit it." and not in the middle, so that "Defaults
to" keeps the env file as its subject. Do not break a code span such as `` `archi create` ``
across two lines. Name the keys only: no `=`, no value, no example value.

### D3. Reading of the issue's scope check

The issue says `git diff --name-only origin/dev` "must print only
`deploy/scripts/README.md`". The nightly flow always commits its OpenSpec change on the
same branch, so the literal command also prints the five files of
`openspec/changes/fix-issue-714-dev-slack-tokens/`. This design reads the check as "the
only file outside this change directory is the README", and runs it as:

```bash
git diff --name-only origin/dev -- . ':(exclude)openspec/changes/fix-issue-714-dev-slack-tokens'
```

That command must print exactly one line: `deploy/scripts/README.md`. The other three
commands of the issue run unchanged:

```bash
grep -n "SLACK_BOT_TOKEN" deploy/scripts/README.md     # at least 1 line
grep -n "SLACK_APP_TOKEN" deploy/scripts/README.md     # at least 1 line
grep -n "SERVICES" deploy/scripts/README.md            # at least 1 line
```

On `82850b3b` all three print nothing (measured), so they are the red check for task 1.1.

## Risks / Trade-offs

- The README text can drift from `lib.sh` when a later change adds a service to a
  deployment. The bullet names the test that pins the `lib.sh` side, so a reader can find
  the source of truth. A drift test is a non-goal (the issue limits the change to the
  README).
- The gate runs no check on this file (no Python line changes, so diff coverage scores
  nothing). The four commands of D3 are the check.
