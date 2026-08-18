## Context

> Line numbers in this section describe `origin/dev` **before** this change — they locate
> the defect. Post-change anchors appear in the Decisions below and in `tasks.md`, and were
> re-derived at the branch head after the final code commit.

`create()` in `src/cli/cli_main.py` runs, in order: a Docker preflight (`:145-155`), the
existing-deployment handling (`:164`), manager construction (`:169-170`), source resolution
(`:172-187`), config validation (`:193`), secret validation (`:196-199`),
`set_sources_enabled` (`:210`), compose-plan construction (`:213-220`), the `--dry` early
return (`:223-239`), and finally the first write to disk — `base_dir.mkdir(parents=True,
exist_ok=True)` at `:243`.

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

The destructive branch is the defect: it executes at `:164`, roughly fifty lines before
anything checks whether the replacement can be built.

### Facts established by reading the code

These were verified rather than assumed. Two of them corrected an earlier draft of this
design, and both corrections came from an adversarial review of that draft:

1. **`create()` is not the only caller.** `evaluate()` calls
   `handle_existing_deployment(base_dir, name, force, False, podman)` at
   `src/cli/cli_main.py:748-750` and then raises "Benchmarking runtime '{name}' already
   exists" at `:752-755` if the directory still exists. It therefore *depends on the
   destructive branch* running at that call site. An earlier draft of this design asserted
   that `create()` was the only caller; that assertion was wrong, and implementing it would
   have broken every `archi evaluate --force`.

2. **Compose-plan construction can refuse the deployment.**
   `ServiceBuilder.build_compose_config()` calls `_discover_repo_path()` when `--dev` is set,
   which raises `ClickException` if no ancestor directory contains `pyproject.toml`
   (`src/cli/utils/service_builder.py:10-18, 198-200`). An earlier draft placed the teardown
   above `build_compose_config` on the grounds that the builder never *reads* `base_dir`
   (`:55, 68, 181, 204`) and so could not pick up stale state. That is true but irrelevant:
   the property that matters is not whether a step reads the old deployment, it is whether
   the step can **fail**. A step that cannot touch the old deployment can still refuse the
   new one, and refusing after the teardown is precisely the defect being fixed.

3. **`base_dir.mkdir()` at `:243` is the first write.** Everything above it is inspection or
   in-memory mutation. `set_sources_enabled()` (`src/cli/managers/config_manager.py:398-415`)
   mutates the in-memory `self.configs` dicts only and writes nothing to disk.

Constraint from `fix-issue-112-dry-run-docker-check`: the Docker preflight is already
positioned above the teardown on purpose, with a comment saying so (`:140-145`). That
placement stays. This change is the same reasoning applied to the checks the earlier fix did
not cover.

## Goals / Non-Goals

**Goals:**

- No step that can refuse the deployment runs after a destructive step.
- Fix the ordering class, so a future required secret or a new validation is protected
  without anyone remembering this issue existed.
- Preserve the `--dry` contract, including when the "would remove" notice does and does not
  appear.
- Preserve today's error precedence for `create` without `--force`.
- Leave `evaluate()` behaviourally identical.

**Non-Goals:**

- Making `create` transactional. If teardown succeeds and the subsequent deployment fails —
  an image pull fails, a port is taken, compose errors — the operator is still left without
  a running deployment. That needs the old deployment preserved and restorable, not a
  reordering, and is out of scope. This change narrows the window to failures that occur
  after everything knowable has been checked; it does not close it. The PR must not describe
  the result as making `--force` safe.
- Fixing the same defect in `evaluate()`. It has its own instance: its teardown at `:748`
  precedes `SecretsManager` construction at `:757`, so a forced evaluate with missing
  secrets destroys the benchmarking runtime and then fails. That is a real bug, but it is
  the benchmarking path with its own test surface, and #287 is scoped to `create`. To be
  filed as a follow-up issue with this evidence rather than folded in silently.
- Changing what counts as a required secret, or adding validations.
- Reformatting `src/cli/managers/secrets_manager.py` (see Decision 4).

## Decisions

### Decision 1: Split the helper, and update both callers explicitly

**Chosen:** separate the two responsibilities into two module-level functions in
`src/cli/utils/helpers.py`:

- `handle_existing_deployment(base_dir, name, force)` — keeps its name and its
  non-destructive precondition: when `base_dir` exists and `force` is falsy, raise the
  existing `ClickException` verbatim; otherwise return. The `dry` and `use_podman`
  parameters are **dropped**, not retained: both were used only by the destructive branch,
  so keeping them would leave two dead arguments at every call site. Both callers are
  updated in this change, so there is no external signature to preserve.
- `remove_existing_deployment(base_dir, name, force, dry, use_podman)` — the destructive
  branch verbatim, including the dry-run notice and the `try/except` that downgrades a
  failed cleanup to a warning. It takes `force` and no-ops when it is falsy, so callers do
  not have to guard it.

`create()` calls the first early and the second late. **`evaluate()` calls both back to
back at its existing call site**, which reproduces today's combined behaviour exactly and
leaves the benchmarking path untouched.

**Alternative considered — move the whole call below validation.** This is issue #287's
approach (a), and is simpler. Rejected on two counts. It silently changes error precedence:
`archi create` without `--force` against an existing deployment with a bad config currently
reports "deployment already exists", and after a wholesale move would report the config
error instead — a worse message, since the operator's actual problem is that they did not
pass `--force`. And it does nothing about `evaluate()`, which would keep its own ordering
defect.

**Alternative considered — leave `handle_existing_deployment` fused and add a separate
precondition function used only by `create()`.** This avoids touching `evaluate()` at all,
which is tempting. Rejected because it leaves a fused function whose destructive half is
reachable from one caller and dead from the other, which is exactly the shape that made this
bug easy to introduce. Updating `evaluate()` to call two explicitly named functions makes
its dependence on the teardown visible at its call site instead of hidden inside a helper.

### Decision 2: Place the teardown below compose-plan construction, immediately above `if dry:`

The teardown call goes after `ServiceBuilder.build_compose_config(...)` (ends `:220`) and
before the `if dry:` branch (`:223`).

**Why not above `build_compose_config`?** Because the builder can refuse the deployment —
see Context fact 2. Teardown above it would leave `archi create --dev --force` outside a
checkout destroying the deployment and then failing on a condition knowable beforehand:
the same defect, moved rather than fixed.

**Why not lower still, immediately before `base_dir.mkdir()` at `:243`?** Because the
`--dry` early return sits at `:239`, so a teardown below it would never execute on a dry run
and the "[DRY RUN] Would remove existing deployment" notice would vanish entirely. That
notice is the dry run reporting its single most consequential effect.

The chosen point is the unique position that is below everything which can refuse and above
the dry-run return. That is why it is chosen — not by preference but by elimination.

### Decision 3: Reject the narrow grafana guard

Issue #287 offers approach (b): mirror `restart()`'s explicit grafana-without-`--env-file`
check (`src/cli/cli_main.py:528-532`) in `create()` before the teardown. Rejected. It closes
one instance while leaving every other required secret — and the compose-plan failure in
Context fact 2, which is not a secret problem at all — destroying first and failing after.
It also duplicates a validation `validate_secrets` already performs, creating a second place
to keep in sync with `service_registry`. The ordering fix subsumes it: once the teardown is
last, the grafana case is handled by the existing `validate_secrets` call with no
special-casing.

### Decision 4: Add the `--env-file` hint in `cli_main.py`, not in `SecretsManager`

Issue #287's acceptance criterion 3 requires the error to name the missing secret and
mention `--env-file`. `validate_secrets` (`src/cli/managers/secrets_manager.py:123-141`)
already names the missing secrets and the env-file path it searched, so half is met. It does
not mention `--env-file`, and under the dummy fallback the message reads

> Missing required secrets in src/cli/managers/secrets_dummy.env: GRAFANA_PG_PASSWORD
> Please add these to your .env file …

which directs the operator to edit a stub inside archi's own package instead of telling them
to pass `--env-file`. That is the actively misleading part.

**Chosen:** add the hint in `create()`, conditional on no `--env-file` having been supplied.

**Why not in `SecretsManager`?** Two reasons, the second decisive:

1. `SecretsManager` is not CLI-aware — it takes a path, not a flag, and naming a click
   option inside it inverts the layering.
2. `src/cli/managers/secrets_manager.py` is **not black-clean**: `black --check` reports
   roughly 81 changed lines. Editing it makes the gate's format step reflow the whole file,
   and `diff-cover --fail-under=80` then measures patch coverage across every reflowed line,
   failing the gate for reasons unrelated to this change. The three files this change does
   touch — `src/cli/cli_main.py`, `src/cli/utils/helpers.py`,
   `tests/unit/test_cli_create_dev_smoke.py` — are all black-clean, verified.

Reformatting `secrets_manager.py` is worth doing but is mechanical churn and belongs in its
own PR per the project's split-churn-from-behaviour rule.

### Decision 5: Verbosity selects diagnostics, never exit status

Added after implementation, from round 2 of the pre-PR adversarial review.

`create()`'s outer handler printed a traceback at `--verbosity 4` and then fell through
without re-raising, so any failure inside the `try` — including the validation failures this
change relies on — exited 0. The deployment survived, and the caller was told the
replacement succeeded.

This predates #287: the Docker preflight is deliberately placed *outside* that `try`, with a
comment saying the handler "swallows exceptions and would report success". The earlier fix
routed around the defect rather than fixing it. That was tolerable while the handler only
masked failures that had already destroyed the deployment; it is not tolerable now, because
"archi refuses instead of destroying" is worth little if the refusal reports success to a
script that then proceeds as though the new deployment were live.

**Chosen:** always raise. Print the traceback additionally at `--verbosity 4`, and re-raise
an existing `ClickException` unchanged rather than re-wrapping it, so its message and exit
code survive intact.

**Alternative considered — move each new check outside the `try`, as #112 did for Docker.**
Rejected: it does not generalise. The validations this change depends on are spread across
forty lines and legitimately belong inside the error handling; hoisting them all out would
duplicate the very structure that made this bug possible. Fixing the handler fixes every
error path in `create()` at once, and no caller can reasonably depend on verbose mode
suppressing a non-zero exit.

**Scope note.** This is strictly wider than #287 as filed — it changes the exit status of
every `create()` failure at `--verbosity 4`, not only the ones this change introduces. It is
included because #287's own acceptance criterion 2 requires the command to "exit non-zero
and leave the existing deployment directory in place", and without it that criterion is
false whenever `-v 4` is passed.

## Risks / Trade-offs

- **The window is narrowed, not closed.** → Failures after the teardown (image pull, port
  conflict, compose error) still leave the operator without a deployment. Stated as a
  Non-Goal and called out in the PR body.

- **`evaluate()` is touched, and it is the benchmarking path.** → Its two calls reproduce
  today's combined behaviour exactly, and a regression test covers `evaluate --force` against
  an existing runtime directory. The risk is real enough that the test is required, not
  optional.

- **Dry-run output changes on a failing dry run.** → `archi create --dry --force` with
  missing secrets currently prints "[DRY RUN] Would remove existing deployment" and then
  fails; afterwards it fails without printing it. This is intentional and specified: a real
  run with those inputs would not have reached the teardown, so reporting that it would have
  removed the deployment was misinformation. Called out because it is a visible change and a
  reviewer will notice it.

- **Log ordering changes on a successful forced re-create.** → "Removing existing deployment
  at ..." now appears after the validation lines instead of before them. No test asserts log
  order, and the new order matches what actually happened.

- **A split function is a wider diff than a moved line, and `helpers.py` is shared.** → Both
  halves stay module-level in the same file, the existing name and both call sites are
  explicit, and the caller inventory is now derived from `grep -rn` rather than assumed —
  which is what caught the `evaluate()` dependency in the first place.

## Migration Plan

None required. No schema, config, image, or API surface changes; no deployment step. The
change is behavioural within two CLI commands, and for `create` only on its failure path.
Rollback is reverting the commit.

Landing order note: `docs/docs/fasrc_archi.md` documents the broken ordering and names #287
as the tracker. That paragraph becomes false when this lands, so its correction belongs in
this PR rather than a follow-up — the alternative is a window in which the docs describe
behaviour the code no longer has.

## Open Questions

None. The two that existed were resolved by reading the code: whether `validate_secrets`
names the missing secret (it does — Decision 4), and whether `create()` is the only caller
of the helper (it is not — Context fact 1).
