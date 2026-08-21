# Derive the host-mode validated port from `external_port` so pre-teardown validation matches the rendered config

## Why

In host mode the port `archi create` **validates** is not the port the deployment
**binds**. Two derivations disagree, and only one of them is the truth.

`TemplateManager._apply_host_mode_port_overrides()`
(`src/cli/managers/templates_manager.py:936-948` on `origin/dev` at `2c404822`) rewrites
each service's effective port before the compose file is rendered:

```python
external = service_cfg.get("external_port")
if external is not None:
    service_cfg["port"] = external
```

So in host mode the effective port is `external_port` whenever that key is present. The
derivation used for **validation**, `_resolve_ports_from_config()` (`:189-206`), reads the
config *before* that override and ignores `external_port` entirely in host mode:

```python
container_port = config_value.get("port", container_port)
host_port = container_port if host_mode else config_value.get("external_port", host_port)
```

Measured on this branch's base (`2c404822`) with
`{"port": 7861, "external_port": 9000}`, `host_mode=True`:

```
_resolve_ports_from_config      -> validated (host, container) = (7861, 7861)
_apply_host_mode_port_overrides -> RENDERED effective port     = 9000
```

Consequence, and it is the consequence #293 just spent a whole change preventing: the
pre-teardown check added by fasrc/archi#293 (`src/cli/cli_main.py`, merged as PR #299)
validates a port the deployment never binds. A duplicate on the real bind target goes
undetected, and a duplicate on the ignored `port` value is reported as a conflict that
does not exist. Because the same `extract_port_config()` output feeds
`_check_ports_available()`, the availability probe also probes the wrong port in host
mode. Fixes fasrc/archi#310.

**This is not a regression from #299.** PR #299 lifted these functions to module level and
added an earlier call site; the host-mode branch is byte-identical to what it replaced.
The adversarial review pass on PR #299 found this and deferred it, because #293's scope was
teardown *ordering* and PR #299 carries a characterization test
(`test_extract_port_config_host_mode_uses_port_for_both`) written specifically to prove the
lift preserved behaviour. Changing which port host mode validates is a behaviour change and
belongs in its own PR — this one.

## What Changes

- The host-mode branch of `_resolve_ports_from_config()` derives the port from
  `external_port` when present, falling back to `port`. It keys off `is not None`, not
  truthiness, because that is exactly what `_apply_host_mode_port_overrides()` does — a
  truthiness test would diverge again on `external_port: 0`.
- Host mode derives **both** the host and the container port from that same value. In host
  mode they are one port: `network_mode: host` makes the compose `ports:` mapping inert
  (`src/cli/templates/base-compose.yaml:262-265`), and the override rewrites `port` itself,
  so a derivation that ran after the override would read the same value for both. See
  design.md D2 for the host-port-only alternative and why it was rejected.
- `_service_port_config_hint()` names the field that was actually validated, so a host-mode
  error message points the operator at the key they must edit. This needs the walked config
  value, which the hint does not currently receive, so the `port_config_path` walk moves
  into one small module-level helper shared by `extract_port_config()` and the hint —
  consistent with #293's standing "one derivation on the validation path" requirement
  rather than a second walk.
- The characterization test `test_extract_port_config_host_mode_uses_port_for_both` is
  renamed and re-asserted in the same commit as the source change. It pins the old
  behaviour on purpose; leaving it would make the commit red.
- **Not BREAKING for any valid deployment.** A host-mode config that sets `port` only is
  unaffected. A host-mode config that sets both keys was already binding `external_port`;
  only the validation and the probe change, to agree with it. What flips is that a real
  host-mode duplicate is now refused (correctly) and a phantom one is no longer reported.

## Out of scope, deliberately

- **`show_service_urls()`** (`src/cli/utils/helpers.py:412`) reimplements the walk on the
  *display* path with divergent fallbacks. That is fasrc/archi#300 and #293's spec already
  excludes it by name. Fixing #300 does not fix this issue, and this change does not fix
  #300.
- **Falsy configured ports being dropped** by `extract_port_config()`'s `if host_port:`
  guard is fasrc/archi#311. The test
  `test_validate_port_config_port_zero_raises_when_reached` pins that boundary on purpose.
  This change must leave it passing untouched.
- Teardown ordering itself (#293, #294). This change moves no call sites.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `cli-create-preflight`: adds a requirement that the port values validated before the
  teardown are the values the rendered deployment binds — closing the one gap #293's
  "one derivation, shared" requirement left open, which guaranteed the two *validation*
  call sites agree with each other but not that either agrees with the rendered config.
  Recorded as an **ADDED** requirement: #293's change
  (`fix-issue-293-validate-ports-before-teardown`) is complete and merged but **not yet
  archived**, so its port requirement is not in `openspec/specs/` and cannot be modified.

## Impact

- `src/cli/managers/templates_manager.py` — the host-mode branch of
  `_resolve_ports_from_config()`; `_service_port_config_hint()` gains the walked config
  value; one new module-level walk helper; `extract_port_config()` calls it instead of
  walking inline. `_apply_host_mode_port_overrides()` is **not** touched — it is the
  correct side. File is black-clean under black 24.10.0 (verified), so the in-place edit
  does not reflow unrelated lines.
- `tests/unit/test_templates_port_checks.py` — new host-mode cases; one existing
  characterization test renamed and re-asserted.
- No changes to `src/cli/cli_main.py`, the compose templates, dependencies, config schema,
  or any deployment. No container rebuild.
