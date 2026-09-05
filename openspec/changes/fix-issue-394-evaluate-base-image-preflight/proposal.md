# Run the base-image preflight on the evaluate path above the teardown

## Why

`archi create` cannot destroy a working deployment and only then fail on a base image it
could never obtain. `archi evaluate` can. Anchors at `origin/dev` `3170498c`, re-verified in
this branch on 2026-09-05:

- `src/cli/cli_main.py:12` — `enforce_base_images` is imported once, for the create path.
- `src/cli/cli_main.py:282` — `base_image_outcomes = enforce_base_images(` — the **only**
  call site in the module.
- `src/cli/cli_main.py:294` — `remove_existing_deployment(` on the create path, below it.
- `src/cli/cli_main.py:890` — `compose_config = ServiceBuilder.build_compose_config(` on the
  evaluate path.
- `src/cli/cli_main.py:900` — `remove_existing_deployment(` on the evaluate path, with a
  literal `False` for `dry`.

Two teardown call sites, one preflight call site. Nothing between `:890` and `:900` can
refuse the run, so `archi evaluate --force` deletes the existing benchmarking runtime and
then discovers the base image is unobtainable. The operator loses a runtime to a failure that
was knowable beforehand — the exact defect fasrc/archi#266 and #287 closed for `create`.

**Measured on this branch, 2026-09-05.** Two new tests were written and run against the
unmodified tree. `test_force_evaluate_with_unobtainable_base_image_keeps_existing_deployment`
fails on its teardown assertion, not on a config or import error:

    AssertionError: runtime was torn down before the refusal:
    [{'deployment_name': 'smoke', 'remove_images': False, 'remove_volumes': False,
      'remove_files': True}]

`test_force_evaluate_with_an_uncoverable_service_template_keeps_existing_deployment` fails on
the refusal message, because the teardown ran and the run died later at the already-exists
guard instead:

    AssertionError: the refusal must name the uncoverable template ...
    Error: Failed due to the following exception: Benchmarking runtime 'smoke' already
    exists at .../archi-home/archi-smoke

Both pass once the call is inserted. The full unit suite is green with the change: **3897
passed, 2 skipped, 1 xfailed** (44s, host, 2026-09-05).

## What Changes

One call, inserted in `evaluate()` between `:890` and `:900`, matching `create()`'s shape at
`:282` and discarding the return value:

```python
enforce_base_images(
    compose_config,
    use_podman=other_flags.get("podman", False),
    dry=False,
)
```

Two new tests in `tests/unit/test_cli_create_dev_smoke.py`, mirroring the create-path
ordering tests at `:1855` and `:1897` for the evaluate path.

No new parameter on `enforce_base_images`, no new entry point, no import change
(`cli_main.py:12` already imports it), no refactor of `create()`, no shared
preflight-then-teardown helper, no `--dry` flag on `evaluate`. The operator settled the
scoping on 2026-09-04: enforce over the **whole declared service set**, exactly as `create`
does. Design D1 records why.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-create-preflight`: one requirement is **ADDED**. The capability's existing lead
  requirement (`openspec/specs/cli-create-preflight/spec.md:6`) says "`archi create` SHALL
  complete every step that is capable of refusing the deployment ... before it performs any
  destructive action". It is scoped to `create` by its own words and its text is unchanged by
  this change, so the evaluate-path guarantee is a new requirement rather than a modification.
  No requirement anywhere under `openspec/specs/` mentions `enforce_base_images` today —
  the base-image preflight work (fasrc/archi#266, #381, #391) is merged but not archived —
  so this delta uses ADDED and never MODIFIED.

## Impact

- `src/cli/cli_main.py` — one statement, five physical lines, inside `evaluate()`. No logic
  is added to `cli_main`: the decision logic stays in `base_image_preflight` per its
  docstring (design D8). The file is black 24.10.0 and isort 6.0.1 clean today.
- `tests/unit/test_cli_create_dev_smoke.py` — two tests appended. No existing test is
  edited. The file has 2191 lines today.
- **The new statement is covered by the existing suite.** Line `:900` does not appear in
  `--cov-report=term-missing`'s Missing list when only the `evaluate` tests run, so the one
  new statement in `src/` reports covered to `diff-cover` before the new tests are counted.
- **The new call runs in every existing `evaluate` test.** All five invoke
  `cli_main.evaluate` (`test_cli_create_dev_smoke.py:1091`, `:1152`, `:1213`, `:1259`,
  `:1312`) and none patches the container probe. Measured: all five still pass. The real
  `ContainerProbe` is constructed but the run refuses or completes as before. Design D3
  records why this is safe and what would change it.
- **Behaviour change for operators.** `archi evaluate --force` can now refuse before the
  teardown on a base image or a service template that this run would not build. That
  refusal is the point of the change and is the same breadth `create` already carries.
- No deployment, no re-ingest, no redeploy. `deploy/**`, `config/**`, and
  `.github/workflows/**` are untouched.
