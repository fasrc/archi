# Design — validate before teardown in `archi evaluate`

## The one decision: where the teardown goes

`remove_existing_deployment(...)` must land **below everything in `evaluate()` that can
refuse the run** and **above the first host mutation**. Those two bounds leave exactly one
seam.

Lower bound — the last step that can refuse is `ServiceBuilder.build_compose_config(...)`
(`cli_main.py:849-857` before the change). The rule is *can this step fail*, not *does this
step read the runtime directory*; #287 got this wrong on its first pass by stopping at
secret validation and missing compose-plan construction, which raises under `--dev` when no
ancestor directory holds `pyproject.toml`.

Upper bound — the first host mutation is `base_dir.mkdir(parents=True, exist_ok=True)`
(`:860`). Between them sits only `template_manager = TemplateManager(env, verbosity)`
(`:859`), whose `__init__` (`templates_manager.py:171-178`) is four pure assignments and a
hook dict. It cannot refuse.

So the teardown can go either side of `TemplateManager(...)`, and the choice is decided by
the test harness rather than by behaviour:

**Place it directly above `TemplateManager(env, verbosity)`.** The unit tests keep a
non-dry evaluate away from the real container runtime by patching
`cli_main.TemplateManager` to raise `RuntimeError(SENTINEL)`
(`tests/unit/test_cli_create_dev_smoke.py:15, :181-183`). A teardown placed *below* that
constructor would never run under the sentinel guard, so the success-path test could not
assert `len(teardowns) == 1` without letting the run touch real volumes and containers.
`create()` already resolves the same tension the same way — its teardown at `:261` sits
above its `TemplateManager` construction, which is why
`test_force_create_still_tears_down_once_validation_passes` can assert both
`len(teardowns) == 1` and `SENTINEL in result.output`.

This is not a behavioural preference dressed up as a testing one: both positions are
behaviourally identical because the constructor cannot fail, and only one of them is
observable by a test that does not mutate the host.

## The `base_dir.exists()` check moves with it

`evaluate()` raises `Benchmarking runtime '{name}' already exists at {base_dir}` at
`:807-810`, immediately below the teardown. It looks like a precondition and is not one.

Without `--force`, `handle_existing_deployment(base_dir, name, force)` at `:802` already
raises when the directory exists, so the check is unreachable on that path. With `--force`,
the teardown has just run — and `remove_existing_deployment()` catches a failed cleanup and
downgrades it to a warning (#287, tasks 2.2). The check is therefore the *only* thing that
turns a silently-failed removal into a refusal instead of letting the run proceed to write a
replacement into a directory it failed to clear.

That meaning is positional. Left at `:807` while the teardown moves to `:859`, the directory
is still present when it runs, and **every** forced evaluate against an existing runtime
would refuse. Issue #290 flags this as the thing most likely to break, and it is right.

Two options were considered:

- **Move the check down with the teardown, keeping it immediately below.** Chosen. The
  post-teardown assertion keeps its exact meaning and its exact message, and the non-force
  path keeps its precedence because `handle_existing_deployment` did not move.
- Make the check conditional on `not force`. Rejected: it inverts the check's purpose. Under
  `not force` the check is dead code, and under `force` — the only path where it does
  anything — it would be skipped. This is the "a check and the operation it guards keying on
  different predicates" mistake #287's review caught twice.

## What was verified, and how

Everything below was measured against `origin/dev` @ `07e007df` with an instrumented
`evaluate()` run, not inferred from reading.

**1. The defect reproduces.** Benchmarking-valid config, env file omitting `PG_PASSWORD`,
`DeploymentManager.delete_deployment` patched to record and remove. Stage trace:

```
['teardown', 'validate_configs:enter', 'validate_configs:ok', 'validate_secrets:enter']
→ ValueError: Missing required secrets in .../secrets.env
```

The teardown runs first; the refusal lands after. Exactly as #290 describes.

**2. A valid run reaches the new teardown position.** Same config, complete env file:

```
['teardown', 'validate_configs:enter', 'validate_configs:ok',
 'validate_secrets:enter', 'validate_secrets:ok',
 'build_compose_config:enter', 'build_compose_config:ok']
```

`build_compose_config` completes, so relocating the teardown below it is reachable and the
success path is not lost. Note this also proves `build_compose_config` does not mind the old
runtime directory still being present — as `create()` already implied, since it calls the
same function above its own teardown.

**3. The existing success-path test cannot survive unmodified.** This is the finding that
would otherwise have surfaced only as a red suite mid-implementation.
`test_force_evaluate_still_removes_existing_runtime` (`:673`) passes
`examples/deployments/basic-openai/config.yaml`, which has no `services.benchmarking` block.
Stage trace with that config:

```
['teardown', 'validate_configs:enter']
→ ValueError: Missing required field: 'services.benchmarking.agent_class'
```

It asserts `len(teardowns) == 1` and passes today *only* because the teardown precedes the
refusal. Move the teardown below `validate_configs` and the assertion becomes unreachable.
The test must get a benchmarking-valid config, or it stops testing the success path it is
named for.

**4. What a benchmarking-valid config requires.** `validate_configs` derives its required
fields from the blank-valued keys of the rendered `base-config.yaml` template
(`config_manager.py:120-140`), which for the benchmarking service is:

```
agent_class, agent_md_file, provider, model, ollama_url
```

`ollama_url` is required **regardless of provider** — the provider-conditional check at
`config_manager.py:328-333` is a second, separate rule with a different message. A config
with `provider: openai` and no `ollama_url` still fails, which is why
`examples/benchmarking/benchmarking_configs/example_conf.yaml` is not usable as-is either
(`provider: local`, no `ollama_url`). `_validate_benchmarking_config` additionally requires
`agent_md_file` to resolve to an existing `.md` file (`:335-348`), so the fixture must point
at a real one such as `examples/agents/cms-comp-ops.md`.

The fixture therefore has to be written by the test rather than borrowed from `examples/`.

## Deliberate non-goals

- **`create()` is not touched.** Its ordering is already correct as of #287.
- **`handle_existing_deployment()` and `remove_existing_deployment()` are not touched.** The
  helper split already exists; this change moves a call site, nothing more.
- **The port-availability probe stays below the teardown.** It runs inside
  `TemplateManager.prepare_deployment_files()` (`templates_manager.py:727-729`), reached from
  `cli_main.py:867`, and the old runtime holds its ports until removal, so it cannot be
  hoisted. fasrc/archi#293 records this constraint for `create()`; nothing here contradicts
  it. Note #293 is in flight in an open PR that edits `templates_manager.py` — no file this
  change touches overlaps it except `cli_main.py`, in a different function.
- **Enumerating refusals is not claimed to be complete.** Every stage of
  `prepare_deployment_files()` still runs after the teardown and any of them can raise.
  fasrc/archi#294 proposes rendering the replacement before destroying the existing
  deployment, which is the structural fix for both commands. The spec delta states this scope
  limit rather than implying the class is closed.
