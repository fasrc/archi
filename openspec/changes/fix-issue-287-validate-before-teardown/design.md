## Context

`create()` in `src/cli/cli_main.py` runs, in order: a Docker preflight (`:145-155`), the
existing-deployment handling (`:164`), manager construction (`:169-170`), source resolution
(`:172-187`), config validation (`:193`), secret validation (`:196-199`), compose-config
construction (`:213-220`), the `--dry` early return (`:223-239`), and finally the first
write to disk — `base_dir.mkdir(parents=True, exist_ok=True)` at `:243`.

`handle_existing_deployment()` (`src/cli/utils/helpers.py:299-325`) does two unrelated
things behind one name:

```python
if base_dir.exists():
    if force:
        if not dry:
            ...delete_deployment(..., remove_files=True)   # destructive
        else:
            logger.info(f"[DRY RUN] Would remove existing deployment at {base_dir}")
    else:
        raise click.ClickException(...already exists... use --force...)   # non-destructive
```

The destructive branch is the defect: it executes at `:164`, roughly thirty lines before
anything checks whether the replacement can be built.

Two facts were verified rather than assumed, because the whole fix rests on them:

1. **Nothing between the teardown and the first disk write depends on the teardown having
   happened.** `ConfigurationManager` and `SecretsManager` read the supplied config files
   and env file, not the deployment directory. `ServiceBuilder.build_compose_config()`
   receives `base_dir` and only stores it (`src/cli/utils/service_builder.py:55, 68, 181,
   204`) — it never reads existing deployment files, so it cannot pick up stale state from
   a not-yet-removed directory.
2. **`base_dir.mkdir()` at `:243` is the first write.** Everything above it is inspection.
   So the teardown can move anywhere in that span without changing what a successful run
   produces.

Constraint from `fix-issue-112-dry-run-docker-check`: the Docker preflight is already
positioned above the teardown on purpose, with a comment saying so (`:140-145`). That
placement stays. This change is the same reasoning applied to the checks the earlier fix
did not cover.

## Goals / Non-Goals

**Goals:**

- No destructive step runs before any validation that could refuse the deployment.
- Fix the ordering class, so a future required secret or a new validation is protected
  without anyone remembering this issue existed.
- Preserve the `--dry` contract exactly, including the "would remove" notice.
- Preserve today's error precedence for `create` without `--force`.

**Non-Goals:**

- Making `create` transactional. If teardown succeeds and the subsequent deployment fails,
  the operator is still left without a running deployment. That is a genuinely harder
  problem — it needs the old deployment preserved and restorable, not merely a reordering —
  and it is out of scope. This change narrows the window to failures that occur after
  validation passes; it does not close it.
- Changing what counts as a required secret, or adding validations.
- Touching `restart()`, `delete()`, or any other subcommand.

## Decisions

### Decision 1: Split the function rather than move the call

**Chosen:** separate `handle_existing_deployment()`'s two responsibilities into two
functions — a non-destructive precondition check that stays at `:164`, and a destructive
teardown that moves below validation.

**Alternative considered — move the whole call below validation.** This is what issue #287
suggests as approach (a), and it is simpler. It was rejected because it silently changes
error precedence for a case unrelated to the bug: `archi create` (no `--force`) against an
existing deployment with a bad config currently reports "deployment already exists", and
after a wholesale move would report the config error instead. The operator's actual problem
is that they did not pass `--force`; telling them about a config file they may not have
intended to deploy is a worse message. Splitting keeps that path byte-identical and confines
the change to the destructive branch, which is the only branch with the defect.

**Cost:** one more function in `helpers.py`, and a name decision. The existing name
`handle_existing_deployment` is retained by the precondition half, so its call site at
`:164` keeps its name and no other caller is affected; the new function carries the
destructive half under an explicit name.

### Decision 2: Place the teardown after secret validation and before compose-config construction

The teardown moves to immediately after `secrets_manager.validate_secrets(...)` /
`config_manager.set_sources_enabled(...)` (`:199-210`), above
`ServiceBuilder.build_compose_config(...)` at `:213`.

**Why not later, immediately before `base_dir.mkdir()` at `:243`?** Because the `--dry`
early return sits at `:239`, so a teardown below it would never execute on a dry run and the
"[DRY RUN] Would remove existing deployment" notice would vanish. That notice is the dry
run reporting its single most consequential effect; dropping it would be a real regression
and is specified against.

**Why not earlier, between validation calls?** Keeping it below *all* validation is the
entire point. Placing it above `build_compose_config` (rather than below) also preserves
today's ordering relationship between teardown and compose construction, so if
`build_compose_config` ever does start reading the deployment directory, this change will
not have been the thing that broke it.

### Decision 3: Reject the narrow grafana guard

Issue #287 offers approach (b): mirror `restart()`'s explicit grafana-without-`--env-file`
check in `create()` before the teardown. Rejected. It closes one instance of the class while
leaving every other required secret — `HUIT_API_KEY`, `OPENAI_API_KEY`, anything a future
service declares — destroying first and failing after. It also duplicates a validation that
`validate_secrets` already performs correctly, creating a second place to keep in sync with
`service_registry`. The ordering fix subsumes it: once validation precedes teardown, the
grafana case is handled by the existing `validate_secrets` call with no special-casing.

The error-message requirement from the issue (name the missing secret, mention `--env-file`)
is satisfied by whatever `validate_secrets` already emits; if that message does not name the
missing secret, improving it is in scope, but adding a parallel guard is not.

## Risks / Trade-offs

- **The window is narrowed, not closed.** → A failure between the teardown and a running
  deployment (image pull fails, a port is taken, compose errors) still leaves the operator
  without a deployment. Stated as a Non-Goal above and called out in the PR body so this
  change is not mistaken for making `--force` safe in general.

- **`build_compose_config` now runs while the old deployment directory still exists.** →
  Verified inert: `service_builder` only stores `base_dir` and never reads from it
  (`:55, 68, 181, 204`). The teardown is nevertheless placed above `build_compose_config`
  rather than below, so the existing ordering between those two is unchanged and this
  reasoning is defence-in-depth rather than load-bearing.

- **Log ordering changes on a forced re-create.** → "Removing existing deployment at ..."
  now appears after the validation log lines instead of before them. No test asserts log
  order, and the new order is more truthful about what actually happened. Worth noting in
  the PR because an operator reading logs side by side with an older run will see it.

- **A split function is a wider diff than a moved line, and `helpers.py` is shared.** →
  Both halves stay module-level in the same file, the existing name and call site are
  retained by the non-destructive half, and `grep` for `handle_existing_deployment` confirms
  the blast radius before committing.

## Migration Plan

None required. No schema, config, image, or API surface changes; no deployment step. The
change is behavioural within a single CLI command, and only on its failure path. Rollback is
reverting the commit.

Landing order note: `docs/docs/fasrc_archi.md` currently documents the broken ordering and
names #287 as the tracker. That paragraph becomes false when this lands, so its correction
belongs in this PR rather than in a follow-up — the alternative is a window where the docs
describe behaviour the code no longer has.

### Decision 4: Add the `--env-file` hint in `cli_main.py`, not in `SecretsManager`

Issue #287's acceptance criterion 3 requires the error to name the missing secret and
mention `--env-file`. Checking what exists: `validate_secrets`
(`src/cli/managers/secrets_manager.py:123-141`) already names the missing secrets and the
env-file path it searched, so half the criterion is met. It does not mention `--env-file`,
and when the dummy fallback is in play the message reads

> Missing required secrets in src/cli/managers/secrets_dummy.env: GRAFANA_PG_PASSWORD
> Please add these to your .env file …

which directs the operator to edit a stub file inside archi's own package instead of telling
them to pass `--env-file`. That is the actually-misleading part.

**Chosen:** add the hint in `create()` in `src/cli/cli_main.py`, conditional on no
`--env-file` having been supplied.

**Why not in `SecretsManager`, where the message is built?** Two reasons, and the second is
the one that decides it if the first is unpersuasive:

1. `SecretsManager` is not CLI-aware. It takes an `env_file_path`, not a flag, and is
   constructed in contexts that have no notion of `--env-file`. Naming a click option inside
   it inverts the layering; the flag is the command's vocabulary, so the command should be
   the thing that mentions it.
2. `src/cli/managers/secrets_manager.py` is **not black-clean** — `black --check` reports
   roughly 81 changed lines across the file. Editing it makes the gate's format step reflow
   the whole file, and `diff-cover --fail-under=80` then measures patch coverage across
   every reflowed line, which fails the gate for reasons unrelated to this change. The three
   files this change does touch — `src/cli/cli_main.py`, `src/cli/utils/helpers.py`, and
   `tests/unit/test_cli_create_dev_smoke.py` — are all black-clean, verified.

Reformatting `secrets_manager.py` is worth doing, but it is mechanical churn and belongs in
its own PR per the project's split-churn-from-behaviour rule. It is not a prerequisite here.

## Open Questions

None. The one open question — whether `validate_secrets` names the missing secret — was
resolved by reading `secrets_manager.py:123-141`; see Decision 4.
