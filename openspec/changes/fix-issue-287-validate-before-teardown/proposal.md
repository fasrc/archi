## Why

`archi create --force` destroys the existing deployment before it validates whether the
replacement is even buildable. `handle_existing_deployment()` runs at
`src/cli/cli_main.py:164` and, with `force=True`, calls
`delete_deployment(..., remove_files=True)` (`src/cli/utils/helpers.py:299-325`) — stopping
the containers and removing the deployment directory. Configuration is not validated until
`:193` and secrets not until `:199`. Every validation failure therefore lands *after* the
running deployment is already gone, leaving it both down and not replaced.

This is reachable by a documented command, not just in theory. `SecretsManager.__init__`
falls back to `src/cli/managers/secrets_dummy.env` when no env file is given
(`src/cli/managers/secrets_manager.py:15-17`), and that file contains exactly one line,
`PG_PASSWORD=donuts`, while the `grafana` service declares
`required_secrets=["GRAFANA_PG_PASSWORD"]` (`src/cli/service_registry.py:114`). So
`archi create --force --services chatbot,grafana` with no `--env-file` is *guaranteed* to
fail `validate_secrets` and guaranteed to destroy the existing deployment first. That
command was published in `docs/docs/fasrc_archi.md` for the FASRC `archi-openai-compat`
deployment until PR #261 corrected it; the docs fix stops operators from copying it, but
the CLI still behaves this way. Fixes fasrc/archi#287.

The codebase already encodes the judgement this change generalises, in two places:

- `restart()` refuses the grafana-without-`--env-file` combination up front, before doing
  anything destructive (`src/cli/cli_main.py:528-532`). `create()` — the path that has the
  destructive step — has no equivalent.
- The Docker preflight carries an explicit comment that it "has to run before
  `handle_existing_deployment()` (a `--force` cleanup tears the old deployment down and
  would leave nothing behind if the runtime is missing)" (`src/cli/cli_main.py:140-145`),
  added by `fix-issue-112-dry-run-docker-check`. That fixed one instance of this class by
  hoisting one check above the teardown. This change fixes the class.

## What Changes

- Split the two responsibilities currently fused in `handle_existing_deployment()`:
  - the **non-destructive precondition** — deployment exists and `--force` was not passed,
    therefore refuse (`helpers.py:321-325`) — stays where it is, so a plain `archi create`
    against an existing deployment still fails fast with the same "already exists" message
    and the same error precedence it has today;
  - the **destructive teardown** — `force=True`, therefore `delete_deployment(...,
    remove_files=True)` — moves below *everything that can refuse the deployment*, which
    includes compose-plan construction and not only secret validation.
- Nothing destructive runs until the deployment is known to be satisfiable. This closes the
  whole class, not the grafana instance: a missing `HUIT_API_KEY`, an invalid config, or a
  compose plan that cannot be built currently destroys first and fails after.
- Update `evaluate()` to call both halves back to back at its existing call site, so the
  benchmarking path is behaviourally identical. `evaluate()` calls the helper at
  `src/cli/cli_main.py:748-750` and then raises "Benchmarking runtime already exists" at
  `:752-755` if the directory survives — it *depends* on the destructive branch, so a split
  that ignored it would break every `archi evaluate --force`.
- No new guard is added to `create()`. Mirroring `restart()`'s explicit grafana check was
  the narrower alternative (issue #287's approach (b)); it is rejected because it closes one
  instance and leaves every other required secret exposed. See `design.md`.
- No change to the `--dry` contract. The teardown call keeps its dry-run branch and still
  logs "[DRY RUN] Would remove existing deployment at ..." before the dry-run summary
  prints, because its new position remains above the `if dry:` early return at
  `cli_main.py:223-239`.
- Fix `create()`'s outer exception handler, which printed a traceback at `--verbosity 4` and
  then fell through without re-raising, so a failed create exited 0. This predates #287 —
  the Docker preflight sits outside that `try` precisely because of it — but it makes this
  change's contract false in verbose mode: the deployment is preserved and the caller is
  told the replacement succeeded. Verbosity now selects diagnostics only.
- Not **BREAKING** for successful runs. A `create --force` that succeeds today succeeds
  identically; the failure path changes, and only by preserving something the operator
  previously lost. The verbosity fix does change the exit status of *every* `create` failure
  at `--verbosity 4`, from 0 to non-zero — a script that treated verbose failures as
  successes will now see them fail, which is the point.

> Line numbers in the **Why** section above describe `origin/dev` before this change; they
> locate the defect. Post-change anchors live in `design.md` and `tasks.md` and were
> re-derived at the branch head after the final code commit.

## Capabilities

### New Capabilities
- `cli-create-preflight`: which preflight checks `archi create` runs, on which code paths,
  and — added here — that no destructive step may precede the validation that could
  cancel it.

  **Sequencing note.** This capability has no file in `openspec/specs/` yet; it is
  introduced by `fix-issue-112-dry-run-docker-check`, which is implemented in code but not
  archived. It is listed as New because that is its true state relative to
  `openspec/specs/`. Both changes' deltas use `## ADDED Requirements` with disjoint
  requirement names, so they merge cleanly into one spec whichever archives first, and
  neither needs to wait for the other.

### Modified Capabilities

(none — no spec in `openspec/specs/` covers CLI create behaviour today)

## Impact

- `src/cli/cli_main.py` — the `create` command: one call split into two, the destructive
  half relocated within the function. The `evaluate` command: one call becomes two adjacent
  calls, preserving its behaviour exactly. No signature changes.
- `src/cli/utils/helpers.py` — `handle_existing_deployment()` split into a precondition
  helper (keeping the existing name and message) and a new teardown helper.
- `tests/unit/test_cli_create_dev_smoke.py` — six regression tests added, modelled on
  `test_force_create_without_docker_keeps_existing_deployment` (`:216-272`), which is the
  same assertion shape for the Docker instance of this class. One of them covers
  `evaluate --force`, without which the helper split would break the benchmarking path
  silently.
- Deliberately **not** in scope, each to be filed as its own issue: `evaluate()`'s own
  instance of this defect (its teardown at `:748` precedes `SecretsManager` at `:757`), and
  reformatting `src/cli/managers/secrets_manager.py`, which is not black-clean and therefore
  currently blocks any behavioural edit to that file via the diff-coverage gate.
- No dependency, API, config, schema, or deployment changes. No container rebuild required.
- `docs/docs/fasrc_archi.md` carries a blockquote describing the current (broken) ordering
  and naming #287 as the tracker. It becomes stale the moment this lands, and issue #288
  tracks updating it. That doc edit belongs in **this** PR, not a follow-up, so the
  behaviour change and the description of it cannot drift apart.
