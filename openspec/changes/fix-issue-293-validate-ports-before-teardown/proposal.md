# Validate port configuration before `archi create --force` tears the deployment down

## Why

`archi create --force` still destroys the existing deployment before it checks the port
configuration, even though fasrc/archi#287 moved the teardown below every *other* check
that can refuse the deployment. The port checks live in
`TemplateManager._check_ports_available()` (`src/cli/managers/templates_manager.py:792`),
which runs inside `_render_compose_file()` (`:725-731`), inside
`prepare_deployment_files()` — i.e. after `remove_existing_deployment()`
(`src/cli/cli_main.py:261-263`) and after `base_dir.mkdir()` (`:286`). So a nonnumeric
port, an out-of-range port, or one port assigned to two enabled services — all knowable
from config alone — still cost the operator a running deployment before the run fails.
`--dry --force` is worse: it returns at `:266` before the checks ever run, reporting
success on a config a real run would refuse. Fixes fasrc/archi#293.

This is the route #287's own review found and deferred (its tasks.md, group 10.1): doing
it properly requires lifting the port derivation and the pure half of the checks so
`create()` can call them without constructing a `TemplateManager`, and that refactor was
judged too large for an already-large PR.

Two constraints make this more than a copy-paste, and both come from #287's review
history:

- **Only the configuration checks can move.** `_check_ports_available()` fuses two jobs:
  pure config checks (`_normalize_port()` rejects bad values at `:873-890`; a
  duplicate-detection pass rejects one port assigned to several enabled services at
  `:839-852`) and an availability probe (`_probe_port()` binds the port, `:892-899`).
  The probe **cannot** move: before the teardown the existing deployment is still running
  and still holds its ports, so probing early would report a false conflict for every
  port the deployment currently uses.
- **The port derivation must be shared, not reimplemented.** The values come from
  `_extract_port_config()` (`:760-790`), which walks `service_def.port_config_path`
  through the config and applies `_resolve_ports_from_config()` defaults (`:921-943`).
  Two independent derivations that must agree is exactly the defect class found twice on
  the #287 PR. This change must leave exactly one implementation of that walk **on the
  validation path** — the pre-teardown call and the pre-probe call share one derivation.
  A pre-existing second walk lives outside the validation path: `show_service_urls()`
  (`src/cli/utils/helpers.py:397-424`, the URL banner printed after a successful create)
  reimplements the walk with *divergent* fallback semantics (host mode falls back to
  `default_container_port` where the derivation here uses `default_host_port`). The
  issue's acceptance criterion 4 ("grep shows no second implementation") is therefore
  unsatisfiable verbatim on `origin/dev` before this change even starts. Consolidating
  that display-path walk would change which URLs get printed — a behaviour change beyond
  this issue's scope — so it is deferred to a follow-up issue this change files, and the
  one-derivation guarantee here is scoped to validation (see design.md D6).

## What Changes

- Lift `_extract_port_config()` and the **pure half** of `_check_ports_available()`
  (normalization + duplicate detection, including the host-mode postgres entry) to
  module-level functions in `src/cli/managers/templates_manager.py`. The existing methods
  become thin delegators, so every existing caller — `restart()` and `evaluate()` reach
  the checks through `prepare_deployment_files()` too — keeps exactly today's behaviour.
  Module level is deliberate: `TemplateManager.registry` is just the module-global
  `service_registry` (`:174`), so no instance is needed, and constructing one early would
  trip the sentinel that `tests/unit/test_cli_create_dev_smoke.py` uses to halt non-dry
  creates before host mutation (see design.md).
- `create()` calls the lifted derivation + pure checks after
  `ServiceBuilder.build_compose_config()` (`src/cli/cli_main.py:242`) and before
  `remove_existing_deployment()` (`:261`). Because the `--dry` early return sits below
  the teardown call (`:266`), this one insertion also makes `--dry --force` with a bad
  port exit non-zero instead of reporting success.
- The availability probe stays exactly where it is, inside `_check_ports_available()`
  after the teardown, and gains a comment stating why it cannot move.
- The pure checks now run twice on a successful create (once pre-teardown, once inside
  `_check_ports_available()` before the probe). Deliberate: they are cheap, idempotent,
  and keeping them inside `_check_ports_available()` is what protects the `restart` and
  `evaluate` paths without new call sites.
- Not **BREAKING**: a create that succeeds today succeeds identically. Failure paths
  change only by failing *earlier* — before the deployment is destroyed — and by dry
  runs now refusing a config a real run would refuse. That last part applies to plain
  `--dry` as well as `--dry --force`: the new call site is unconditional and sits above
  the `--dry` return, so any dry run with an invalid or duplicated host port flips from
  exit 0 to non-zero. That flip is the point — the dry run previously misreported what
  the real run would do.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `cli-create-preflight`: the "No destructive step precedes a step that can refuse the
  deployment" requirement adds the configuration-only port checks to the enumerated
  pre-teardown steps and drops the port carve-out from its "Scope, stated honestly"
  paragraph (the general `prepare_deployment_files()` problem remains tracked as
  fasrc/archi#294). A new requirement pins the split: pure port checks before the
  teardown, the availability probe after it, one derivation shared by both validation
  call sites (the display-path walk in `show_service_urls()` is out of scope and
  tracked by a follow-up issue).

## Impact

- `src/cli/managers/templates_manager.py` — lifted to module level:
  `extract_port_config` (from `_extract_port_config`), `validate_port_config` (the pure
  half of `_check_ports_available`), and the helpers they call — `_normalize_port`,
  `_service_port_config_hint`, and `_resolve_ports_from_config` (the last is called by
  `_extract_port_config`'s body at `:776`, so a lift that omits it is a `NameError`).
  `_check_ports_available` itself stays a method: it delegates the pure half and keeps
  the probe loop. File is black-clean (verified with black 24.10.0), so the in-place
  edit does not reflow unrelated lines.
- `src/cli/cli_main.py` — one import line plus one pre-teardown call in `create()`. No
  signature changes, no `TemplateManager` construction before the teardown.
- `tests/unit/test_cli_create_dev_smoke.py` — three regression tests modelled on
  `test_force_create_with_unbuildable_compose_plan_keeps_existing_deployment` (`:465`):
  invalid port value, duplicated port, and `--dry --force` with an invalid port.
- New `tests/unit/test_templates_port_checks.py` — direct unit tests for the lifted
  functions. Required for the gate, not just hygiene: no existing unit test exercises
  any of the port-check code (verified by grep), so the moved lines would otherwise have
  zero diff coverage.
- No dependency, API, config, schema, or deployment changes. No container rebuild.
