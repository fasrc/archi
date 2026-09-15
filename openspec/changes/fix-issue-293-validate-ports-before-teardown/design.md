# Design — validate port configuration before the `--force` teardown

## Context

> Line anchors below describe `origin/dev` at `cdd6e35d` (this branch's base). They locate
> the defect and the seams; re-derive anchors at the branch head before citing them in the
> PR, because this change's own commits will shift them.

### The ordering today

`create()` in `src/cli/cli_main.py` runs, in order: the no-`--force` refusal
(`handle_existing_deployment`, `:178`), config validation (`:215`), secret validation
(`:222`), compose-plan construction (`:242-250`), the `--force` teardown
(`remove_existing_deployment`, `:261-263`), the `--dry` early return (`:266-282`),
`TemplateManager` construction (`:285`), `base_dir.mkdir()` (`:286`), and
`prepare_deployment_files()` (`:293`). The port checks run inside the last of these —
`_render_compose_file()` calls `_extract_port_config()` then `_check_ports_available()`
(`src/cli/managers/templates_manager.py:725-731`) — two steps after the deployment was
destroyed.

### Facts established by reading the code

1. **The pure/probe split already exists inside `_check_ports_available()`**
   (`templates_manager.py:792-865`), it is just not exposed. The method builds
   `port_usages` by normalizing every enabled service's host port (`:806-822`), appends a
   postgres entry in host mode from `services.postgres.port` (`:824-834`), groups usages
   into `port_to_services` (`:839-841`), and collects duplicate-assignment errors
   **unconditionally** (`:843-852`). Only then, and only when `allow_port_reuse` is
   falsy, does it probe each port (`:854-862`). So: normalization + duplicates are pure
   functions of (plan, config); the probe is the only part that touches the host.

2. **`allow_port_reuse` gates the probe only.** Duplicate detection runs regardless
   (`:843-852` sit outside the `if not allow_port_reuse:` at `:854`). The lifted pure
   check therefore takes no `allow_port_reuse` parameter, and the pre-teardown call site
   in `create()` needs nothing from `other_flags`.

3. **No `TemplateManager` instance is needed.** The only instance state the port code
   touches is `self.registry`, which `__init__` sets to the module-global
   `service_registry` (`templates_manager.py:174`); `service_registry` is already
   imported at module scope (`:14`). Everything else comes from the two arguments the
   lifted functions will take: the `DeploymentPlan` (`plan.host_mode`,
   `plan.get_enabled_services()`, `plan.get_service(...)`) and the config manager
   (`config_manager.get_configs()`).

4. **`compose_config` in `create()` IS the `DeploymentPlan`.**
   `ServiceBuilder.build_compose_config()`'s return value at `cli_main.py:242` is what
   `prepare_deployment_files(plan, ...)` receives at `:293`
   (`templates_manager.py:180-192`). So the pre-teardown call site has both arguments in
   scope with no new construction.

5. **The test sentinel forbids early `TemplateManager` construction.**
   `tests/unit/test_cli_create_dev_smoke.py` halts non-dry creates by patching
   `cli_main.TemplateManager` to raise (`:183`, `:643`, `:848`).
   `test_force_create_still_tears_down_once_validation_passes` (`:623`) asserts the
   teardown DID run before the sentinel fires; constructing a `TemplateManager` in
   `create()` above the teardown would fire the sentinel first and break it. This is why
   the seam is module-level functions, exactly as the issue prescribes.

6. **No existing unit test exercises any port-check code.** `grep -rn
   '_check_ports_available\|_extract_port_config\|_normalize_port\|allow_port_reuse\|_probe_port'
   tests/unit/` returns nothing. Consequence for the gate: the lifted function bodies
   count as changed lines with zero incumbent coverage, so this change must bring direct
   unit tests or diff-cover fails at 80%.

7. **The files to be edited are black-clean** (`black --check` with the gate's 24.10.0:
   `templates_manager.py`, `cli_main.py`, `test_cli_create_dev_smoke.py` all unchanged),
   so in-place edits will not reflow unrelated lines into the diff.

8. **A second `port_config_path` walk already exists, outside the validation path.**
   `show_service_urls()` (`src/cli/utils/helpers.py:397-424`) — the URL banner printed
   by `log_deployment_success()`, which `create()` reaches at `cli_main.py:311` —
   independently walks `service_def.port_config_path.split(".")` with **divergent**
   fallback semantics: in host mode it falls back to `default_container_port`
   (`helpers.py:409`) where `_extract_port_config` uses `default_host_port`. So the
   issue's acceptance criterion 4 ("grep shows no second implementation of the
   port_config_path walk") is unsatisfiable verbatim — it is violated on `origin/dev`
   before this change starts, by code this issue's fix has no reason to touch. See D6.

9. **What the checks validate today — precisely.** Only *host-side* port values reach
   `_normalize_port`: `_check_ports_available` normalizes `{service}_port_host` entries
   only (`templates_manager.py:811-822`), and in non-host mode the host port comes from
   `external_port` while a bad container `port` value flows unchecked into the compose
   template. Falsy values are dropped even earlier: `if host_port:` at `:785` means a
   configured `port: 0` (out of range per `_normalize_port`) or empty string silently
   yields *no* port entry rather than an error. This change lifts that behaviour
   verbatim and does not extend it — the spec scenarios are scoped to truthy host-side
   values accordingly, and the smoke tests MUST use `--hostmode` (or set
   `external_port`) for a bad `port` value to be refusable at all.

10. **`create()`'s outer handler preserves the message.** A `ValueError` from the
    checks reaches the handler at `cli_main.py:319-330`, which re-raises
    `click.ClickException(str(e))` — non-zero exit, original text in the CLI output —
    so smoke tests can assert on message substrings through `CliRunner`.

## Decisions

### D1 — Seam: module-level functions in `templates_manager.py`, methods delegate

Lift five things to module level in `src/cli/managers/templates_manager.py`:

- `extract_port_config(plan, config_manager) -> Dict[str, Any]` — the body of
  `_extract_port_config()` verbatim, with `self.registry` → `service_registry` and
  `self._resolve_ports_from_config` → the lifted module function.
- `validate_port_config(plan, config_manager, port_config) -> tuple[Dict[int, list],
  list[str]]` — the pure half of `_check_ports_available()`: build `port_usages`
  (including the host-mode postgres entry; normalization failures raise from
  `_normalize_port` immediately, exactly as today), group into `port_to_services`,
  collect duplicate-assignment error strings. Returns `(port_to_services, errors)`
  and does NOT raise on duplicates — error-list accumulation is the caller's job, so
  the combined-message behaviour below can be preserved byte-for-byte.
- `_normalize_port(...)`, `_service_port_config_hint(...)`, and
  `_resolve_ports_from_config(...)` — pure helpers the above call. The last is easy to
  miss: `_extract_port_config`'s body calls it via `self` at `templates_manager.py:776`,
  so a "verbatim" lift that omits it is a `NameError` at the first config-driven port.

The existing methods become delegators: `_extract_port_config(context)` returns
`extract_port_config(context.plan, context.config_manager)`;
`_check_ports_available(context, port_config, *, allow_port_reuse=False)` calls
`validate_port_config(...)`, then (when `allow_port_reuse` is falsy) runs the existing
probe loop over the returned mapping appending to the same `errors` list, and finally
raises the single combined `ValueError("Port check failed:\n" + ...)` when the list is
non-empty — **byte-identical to today**, including the case where duplicate-assignment
errors and port-in-use errors co-occur in one message. A design that raised on
duplicates before probing would silently drop the "already in use" diagnostics from
that combined message, contradicting "keeps exactly today's behaviour".

Alternatives rejected:
- **Staticmethods on `TemplateManager`** — callable without an instance, but invites the
  next contributor to reach them through an instance, and the issue explicitly steers to
  a seam that never risks early construction (fact 5).
- **A new module** — nothing forces it; `cli_main.py` already imports from
  `templates_manager` (`:14`), there is no import cycle (templates_manager does not
  import cli_main), and keeping the functions beside their consumers keeps the diff
  minimal.

### D2 — Call site: between the compose plan and the teardown

`create()` gains, after `build_compose_config` (`cli_main.py:242-250`) and before
`remove_existing_deployment` (`:261`):

```python
_, port_errors = validate_port_config(
    compose_config, config_manager, extract_port_config(compose_config, config_manager)
)
if port_errors:
    raise ValueError("Port check failed:\n" + "\n".join(port_errors))
```

(same framing string as `_check_ports_available`'s raise — extract a tiny shared helper
or accept the one-line duplication, implementer's choice), with a comment stating the
invariant (pure port checks can refuse the deployment, so they precede the teardown; the
availability probe cannot move — see D3). Because the `--dry` return sits below the
teardown call, this single insertion fixes dry runs reporting success on a refusable
config — for plain `--dry` as well as `--dry --force`, since nothing gates the call on
`force`. No separate dry-run branch needed.

The `ValueError` (raised here or by `_normalize_port` inside the derivation) propagates
to `create()`'s outer handler (`cli_main.py:319-330`), which after #287 always
re-raises — wrapping non-Click exceptions as `click.ClickException(str(e))` — so the
command exits non-zero with the original message, which already names the offending
value, service, and config path (`_normalize_port`, `templates_manager.py:873-890`).

### D3 — The probe does not move, and says why

`_probe_port()` binds each port to detect conflicts. Before the teardown the existing
deployment is still running and still holds its ports, so an early probe would report a
false conflict for every port the old deployment uses — refusing precisely the
re-creates that should succeed. The probe therefore stays inside
`_check_ports_available()`, post-teardown, with a comment making the reason durable.
This is the nuance the issue calls out as the whole point of the split.

### D4 — The pure checks run twice on a successful create, deliberately

Pre-teardown in `create()`, and again inside `_check_ports_available()` before the probe.
Removing the second run would strip validation from every other path into
`prepare_deployment_files()` — `restart()` (`cli_main.py:600`) and `evaluate()` (`:868`)
— to save two cheap, side-effect-free passes over a dict. Not worth the coupling.

### D5 — Test placement

- Ordering/regression tests go in `tests/unit/test_cli_create_dev_smoke.py`, modelled on
  `test_force_create_with_unbuildable_compose_plan_keeps_existing_deployment` (`:465`):
  existing deployment with a marker file, `_record_teardowns(monkeypatch)`, CliRunner
  invocation, assert exit non-zero + `teardowns == []` + marker intact + the error text
  in `result.output` (the scenario's THEN clause names the value/service/port — a
  generic error must not pass). Two recipe requirements the model test does NOT
  exhibit, both load-bearing:
  1. **Patch the sentinel too** — `monkeypatch.setattr(cli_main, "TemplateManager",
     _stop_before_host_mutation)`. The model test omits it only because its failure
     fires pre-teardown even on old code. A bad-port failure on UNFIXED code fires
     seven stages deep in `prepare_deployment_files`, so the mandated red run would
     otherwise sail through `base_dir.mkdir`, `write_secrets_to_files`, and
     `VolumeManager.create_required_volumes` — which shells out real
     `docker volume create` — violating the suite invariant (test file `:13-16`) and
     the issue's "do not run a non-dry create against a real deployment". With the
     sentinel, the red run still fails on `teardowns != []` without touching the host,
     and on fixed code the pre-teardown check fires before the sentinel is reachable.
  2. **Pass `--hostmode`** (or set `external_port` instead of `port`) — see fact 9: in
     default mode a bad `port` value never reaches `_normalize_port`, so a test without
     `--hostmode` is red before AND after the fix, i.e. red for the wrong reason.
  Bad-port configs are produced by copying the example config to `tmp_path` and setting
  the chatbot service's port to a nonnumeric value / a duplicate of another enabled
  service's port. Use the `archi_home` fixture (it patches `cli_main.ARCHI_DIR`, which
  freezes at import).
- Function-level tests for `extract_port_config` / `validate_port_config` go in a new
  `tests/unit/test_templates_port_checks.py`, constructing plans via
  `ServiceBuilder.build_compose_config` or a minimal `DeploymentPlan` — whichever the
  existing test suite already does for plan objects (follow precedent found there).
  These carry the diff coverage for the lifted bodies (fact 6). They must cover both
  values of `host_mode` (the dict branch of `_resolve_ports_from_config` forks on it:
  `port` vs `external_port`, `templates_manager.py:933-939`), the registry-defaults
  fallback when the config omits a service's section (`:782-783`), and the negative
  case that a DISABLED service's invalid or conflicting port does NOT refuse
  (`validate_port_config` filters to `plan.get_enabled_services()` at `:807` while
  `extract_port_config` iterates the whole registry at `:765` — a lift that iterated
  the port_config keys instead would refuse valid creates and pass every positive test).
- The delegator `_check_ports_available` needs direct tests too (with `_probe_port`
  monkeypatched — no real binds): probe runs when `allow_port_reuse` is falsy; probe
  skipped when truthy while duplicate detection still refuses (the behaviour `restart()`
  depends on); and the combined duplicate+in-use message is byte-identical to today's.
  Without these, the probe half of the requirement rests on a comment (task 3.1) and
  the reworked delegator lines land with zero diff coverage.

### D6 — Scope of "one derivation": the validation path, not all of `src/`

The one-derivation guarantee is scoped to the validation path: the pre-teardown call in
`create()` and the pre-probe call in `_check_ports_available()` share
`extract_port_config`. The pre-existing display-path walk in `show_service_urls()`
(fact 8) stays untouched, for two reasons: its fallback semantics deliberately-or-not
differ (consolidating it would change which URLs the success banner prints — a
behaviour change with no red test in this issue's scope), and it cannot destroy a
deployment (it runs after a successful create). It is a real instance of the
two-derivations defect class, so it gets a follow-up issue (filed as part of tasks
group 3) rather than a silent pass. The spec scenario and task 3.2's grep verification
are worded for this scope; the PR body must state the deviation from the issue's
acceptance criterion 4 verbatim-reading and link the follow-up.

## Risks

- **Behaviour drift during the lift.** Mitigated by moving bodies verbatim, keeping
  delegators, and the direct unit tests asserting the same errors (same messages) the
  methods raise today.
- **A config shape the example config does not exhibit** (e.g. `port_config_path`
  pointing at a scalar vs a dict) behaving differently pre- and post-lift. Mitigated by
  unit-testing both shapes through `_resolve_ports_from_config`'s two branches
  (`templates_manager.py:933-941`).
- **Line-anchor rot in artifacts.** Anchors here describe the base commit; tasks.md
  requires re-deriving any anchor cited in the PR at the branch head.
