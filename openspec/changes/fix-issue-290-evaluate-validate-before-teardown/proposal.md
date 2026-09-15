## Why

`archi evaluate --force` destroys the existing benchmarking runtime before it validates
whether the replacement is even satisfiable. On `origin/dev` @ `07e007df`:

| step | `src/cli/cli_main.py` | can it refuse? |
|---|---|---|
| `preflight_benchmark_configs(...)` — already correctly above the teardown | `:790` | yes |
| `handle_existing_deployment(base_dir, name, force)` — non-destructive precondition | `:802` | yes |
| `remove_existing_deployment(...)` — **destructive** | `:803-805` | — |
| `if base_dir.exists(): raise "Benchmarking runtime '{name}' already exists"` | `:807-810` | yes |
| `SecretsManager(env_file, config_manager)` | `:812` | no |
| source resolution, then `disabled_conflicts` refusal | `:819-827` | yes |
| `config_manager.validate_configs(...)` | `:832` | yes |
| `secrets_manager.validate_secrets(...)` | `:834-837` | yes |
| `ServiceBuilder.build_compose_config(...)` | `:849-857` | yes |
| `TemplateManager(env, verbosity)` | `:859` | no — pure assignment |
| `base_dir.mkdir(parents=True, exist_ok=True)` — first host mutation | `:860` | — |

Five steps that can refuse the run sit *below* the destructive one. A forced evaluate whose
secrets are missing therefore removes the benchmarking runtime and then fails, so the
operator loses the runtime and gets nothing back. Fixes fasrc/archi#290.

This is the same defect fasrc/archi#287 closed in `create()`, left in place deliberately:
#287's PR split `handle_existing_deployment()` into a non-destructive precondition and a
destructive `remove_existing_deployment()`, and updated `evaluate()` to call both back to
back **specifically to preserve its behaviour byte-for-byte**, so a PR about `create` did not
also change the benchmarking path. `create()` now shows the target shape on the same trunk —
precondition at `:178`, `SecretsManager` at `:183`, teardown at `:261`. The refactor that
makes this fix small is already landed; what remains is moving one call.

**Reproduced, not inferred.** With a benchmarking-valid config and an env file omitting
`PG_PASSWORD`, a forced evaluate reaches `validate_secrets` and raises "Missing required
secrets" — with the teardown already done. Measured stage trace:
`['teardown', 'validate_configs:enter', 'validate_configs:ok', 'validate_secrets:enter']`.

## What Changes

- Move `remove_existing_deployment(...)` in `evaluate()` from directly below
  `handle_existing_deployment(...)` to directly below `ServiceBuilder.build_compose_config(...)`
  and directly above `TemplateManager(env, verbosity)`. `handle_existing_deployment(...)`
  stays at `:802`, so a plain `archi evaluate` against an existing runtime still refuses
  first with the same message and the same error precedence it has today.
- Move `evaluate()`'s `if base_dir.exists(): raise "Benchmarking runtime already exists"`
  check down with the teardown, so it stays immediately below it. It is a post-teardown
  assertion, not a precondition: `remove_existing_deployment()` downgrades a failed cleanup
  to a warning, and this check is what turns that warning into a refusal. Left where it is,
  it would fire on **every** forced evaluate, because the directory is still present at
  `:807` once the removal moves down.
- Correct `tests/unit/test_cli_create_dev_smoke.py::test_force_evaluate_still_removes_existing_runtime`,
  which cannot survive this change unmodified. It invokes evaluate with
  `examples/deployments/basic-openai/config.yaml`, which declares no
  `services.benchmarking` block, so `validate_configs` raises `Missing required field:
  'services.benchmarking.agent_class'`. Today its `len(teardowns) == 1` assertion passes
  only because the teardown happens *before* that refusal. Measured stage trace on
  `origin/dev`: `['teardown', 'validate_configs:enter']` — it never reaches
  `validate_secrets` or `build_compose_config` at all. Once the teardown moves below them,
  the assertion becomes unreachable and the test fails. It needs a benchmarking-valid config
  to keep asserting what its name claims.
- No new guard, no new refusal, and no change to which inputs `evaluate` accepts. Only the
  position of one destructive call changes, and only the failure path is observably
  different — by preserving something the operator previously lost.
- Not **BREAKING**. A forced evaluate that succeeds today succeeds identically.

> Line numbers above describe `origin/dev` @ `07e007df` before this change; they locate the
> defect. Post-change anchors live in `design.md` and `tasks.md` and must be re-derived at
> the branch head after the final code commit.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `cli-create-preflight`: gains the `archi evaluate` half of the invariant it already states
  for `archi create` — no destructive step may precede a step that can refuse the run. The
  capability already reaches into `evaluate()`, in the requirement "Splitting the teardown
  helper preserves every existing caller", so this is the right home rather than
  `benchmark-bank-preflight` (which owns question-bank schema validation, not teardown
  ordering).

  That existing requirement is **modified**, not merely added to. Its scenario asserts that a
  forced evaluate removes the runtime "exactly as it does today", which was true of #287 and
  is deliberately no longer true here: the removal now happens later in the sequence. Leaving
  it unchanged would leave the spec asserting the ordering this change exists to correct.

## Impact

- `src/cli/cli_main.py` — the `evaluate` command only. One destructive call and one
  post-teardown assertion relocated within the function. No signature changes, no changes to
  `create()`, and no changes to `src/cli/utils/helpers.py`.
- `tests/unit/test_cli_create_dev_smoke.py` — one existing test corrected (config fixture),
  and three regression tests added: a missing-secret case, an invalid-config case, and the
  no-`--force` precedence guard.
- The port-availability probe inside `TemplateManager.prepare_deployment_files()`
  (`src/cli/managers/templates_manager.py:727-729`) stays **below** the relocated teardown,
  which is required: the old runtime still holds its ports until it is torn down, so that
  probe genuinely cannot move above the removal. This is the constraint recorded in
  fasrc/archi#293 and it is respected rather than worked around.
- No dependency, API, config, schema, or deployment changes. No container rebuild required.
- Out of scope, already tracked: fasrc/archi#294 proposes rendering the replacement before
  destroying the existing deployment, which is the structural answer for both commands.
  This change closes the enumerated refusals in `evaluate`, exactly as #287 did for `create`,
  and claims no more than that.
