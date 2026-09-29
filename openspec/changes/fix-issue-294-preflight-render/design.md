## Context

Anchors at `origin/dev` `ac3e7d1a` (grep the symbol if a line moved):

- `src/cli/cli_main.py` `create()`: `handle_existing_deployment` `:187`, `enforce_base_images`
  `:282`, `remove_existing_deployment(` `:294`, `if dry:` `:299`,
  `TemplateManager(env, verbosity)` `:322`, `base_dir.mkdir` `:323`,
  `write_secrets_to_files` `:325`, `create_required_volumes` `:328`,
  `prepare_deployment_files(` `:330`, `start_deployment` `:340`. The outer
  `except Exception` at `:356` turns any non-click exception into
  `click.ClickException(str(e))`, so a re-raised stage error keeps its message.
- `evaluate()`: `handle_existing_deployment` `:850` (stays first, for error precedence), the
  #294 comment `:835-849`, `enforce_base_images` `:900`, `remove_existing_deployment(`
  `:906`, the `base_dir.exists()` refusal `:910`, `TemplateManager(...)` `:915`, `mkdir`
  `:916`, secrets `:918`, volumes `:921`, `prepare_deployment_files(` `:923`, start `:931`.
- `src/cli/managers/templates_manager.py`: `TemplateContext` `:187`, `base_dir` set from
  `plan.base_dir` in `__post_init__` `:196-197`; `build` property `:227` reads
  `options["build"]` (default `True`); `prepare_deployment_files` `:475`; `_build_workflow`
  `:504` (ten stages, `_stage_benchmarking` appended when `benchmarking`);
  `_stage_source_copy` `:749` returns early when `not context.build`;
  `_render_compose_file` `:1097` reads `allow_port_reuse`; `_check_ports_available` `:1136`
  runs the pure `validate_port_config` and then, unless `allow_port_reuse`, the live
  `_probe_port` bind `:1153`.
- Every stage writes under `context.base_dir`; no template interpolates `base_dir`
  (0 hits in `src/cli/templates/`); compose mounts are relative.
- `src/cli/managers/volume_manager.py:29` `_create_volume` is idempotent.

Measured on this host at `ac3e7d1a` (Loop 1): a real non-dry `create` of
`examples/deployments/basic-openai/config.yaml` (the smoke tests' `EXAMPLE_CONFIG`), with
`VolumeManager.create_required_volumes` and `DeploymentManager.start_deployment` patched out,
**failed in `_check_ports_available`** with `Port 7866 is already in use` — a real deployment
on this host holds the ports. With `_probe_port` patched to return `None` the same run
rendered the whole tree (`compose.yaml`, `configs/`, `data/`, `init.sql`, `migrations/`,
`weblists/`, `archi_code/`, `SOURCE_COMMIT`) and reached `start_deployment`. So the full
render works in the unit-test environment, and the live port probe is exactly the check a
pre-teardown render must not run.

## Goals / Non-Goals

**Goals:** any deterministic render failure refuses `create --force` and `evaluate --force`
before the teardown; `--dry` reports the same failure a real run would hit; the real render
after the preflight is unchanged.

**Non-Goals:** transactional `compose up`; rollback; a staging swap; the live port probe
before teardown; removing early checks (they give better messages and stay).

## Decisions

**D1 — One stage runner, two targets.** Extract the loop in `prepare_deployment_files` into
`_run_workflow(self, context)`. `prepare_deployment_files` builds its `TemplateContext` as
today and calls it. `preflight_render(self, plan, config_manager, secrets_manager, **options)`
builds a `TemplateContext` with `options = {**options, "build": False, "allow_port_reuse": True}`,
opens `tempfile.TemporaryDirectory(prefix="archi-preflight-")`, sets
`context.base_dir = Path(tmp)` after construction, and calls `_run_workflow`. The same stage
list runs, so a stage added later is covered with no edit here. A method, not a flag on
`prepare_deployment_files`: the two calls have different contracts (write vs discard) and a
flag would let one call site pick the wrong one silently.

**D2 — The source copy is skipped with `build=False`.** `_stage_source_copy` already returns
when `not context.build`. It is large and cannot fail on config input. The real render is
unaffected: its options are the caller's own.

**D3 — The live port probe is skipped; the pure port check is not.** The existing deployment
still holds its ports before the teardown, so a probe there refuses every re-create that
reuses them (the comment at `_check_ports_available` says so, and the Loop-1 measurement
reproduced it). `allow_port_reuse=True` skips only `_probe_port`; `validate_port_config`
still raises on an invalid or duplicated port. A port held by something else is still found
after the teardown, as today — documented, not fixed.

**D4 — Failures re-raise unchanged.** The first stage exception propagates as-is (no
wrapping), after one `logger.error` line naming the stage (`stage.__name__`) and saying the
existing deployment was not changed. The callers' `except` blocks already turn it into a
`ClickException` with the original message, so every existing message assertion
(`Invalid port value`, `agents_dir`, …) keeps working. The temporary directory is removed on
success and on failure (the context manager guarantees it).

**D5 — Call-site order.** In both commands the new order is: existing validations →
`enforce_base_images` → `template_manager = TemplateManager(env, verbosity)` →
`template_manager.preflight_render(compose_config, config_manager, secrets_manager, **other_flags)`
→ (not dry) `VolumeManager(...).create_required_volumes(compose_config)` (volumes only) →
`remove_existing_deployment(...)` → `if dry:` summary and return (create only) →
`volume_manager.stage_local_files(...)` (create only; it copies into the data-manager volume
that the running deployment still mounts, so it must not precede the teardown) →
`base_dir.mkdir` → secrets →
`template_manager.prepare_deployment_files(...)` (same instance) → start. `evaluate()`
keeps `handle_existing_deployment` first and the `base_dir.exists()` refusal right after the
teardown. `--dry` creates no volumes and no directories: the preflight writes only under the
system temp directory, which it removes. `--dry` without `--force` runs the preflight too.

**D6 — Three smoke tests move their stop point.** Eight tests in
`tests/unit/test_cli_create_dev_smoke.py` replace `cli_main.TemplateManager` with a callable
that raises `SENTINEL`, as a "stop after the teardown" marker. After D5 the constructor runs
before the teardown. Five of them refuse earlier and assert `teardowns == []`, so they stay
green unchanged — do not edit them. Three assert the teardown *did* run and break:
`test_force_create_still_tears_down_once_validation_passes`,
`test_force_evaluate_still_removes_existing_runtime`, and
`test_force_create_continues_when_teardown_fails`. In each, replace the constructor sentinel
with: `TemplateManager.preflight_render` patched to a recorder that appends to a shared
`events` list and returns `None`; `TemplateManager.prepare_deployment_files` patched to raise
`RuntimeError(SENTINEL)`; `VolumeManager.create_required_volumes` patched to a no-op; and
the teardown recorder appending to the same `events`. Keep every existing assertion and add
one: `events` shows the preflight before the teardown. They test ordering, not rendering, so
a recorder is honest there. Do not weaken any other test.

**D7 — Tests are stage-agnostic.** The class test patches `TemplateManager._build_workflow`
to return one stage that raises `ValueError("late-render-sentinel")` — it names no real
stage. It must pass for any stage the list grows to include.

**D8 — Idempotence is tested, not assumed.** The preflight runs the same stages on the same
`config_manager` and `plan` objects the real render uses next, so a stage that mutates them
in place would change the real output. `_render_config_files` deep-copies today, but that is
not a contract. A test renders the example config twice into two directories — once plain,
once after a `preflight_render` on the same objects — and asserts the two trees are
byte-identical (excluding `archi_code/` and `SOURCE_COMMIT`, and with `_probe_port` patched
to `None`).

## Risks / Trade-offs

- Render cost is paid twice (without the source copy). Measured under 1 s on the example
  config.
- `get_git_version()` and the benchmarking stage's `git` subprocesses run twice. They are
  read-only.
- A non-deterministic failure (disk full, a port taken by another process, an image pull in
  `compose up`) can still happen after the teardown. The docs keep saying `--force` is not
  transactional past `compose up`.
