# Reject a configured-but-falsy port instead of dropping it before the `create --force` teardown

## Why

`extract_port_config()` filters on **truthiness** before anything validates, so the three
values `_normalize_port()` was written to reject never reach it.

The guards are `src/cli/managers/templates_manager.py:235,237` on `origin/dev` at
`2c404822`:

```python
if host_port:
    port_config[f"{key_prefix}_port_host"] = host_port
if container_port:
    port_config[f"{key_prefix}_port_container"] = container_port
```

`validate_port_config()` then skips any service whose `_port_host` key is absent (`:261`):

```python
host_port = port_config.get(f"{key_prefix}_port_host")
if host_port is None:
    continue
```

Measured on this branch's base (`2c404822`), host mode, `services.chat_app`:

```
port=0          kept_by_extract=False  errors=[]  -> create PROCEEDS to teardown
port=''         kept_by_extract=False  errors=[]  -> create PROCEEDS to teardown
port=None       kept_by_extract=False  errors=[]  -> create PROCEEDS to teardown
port='notaport' kept_by_extract=True   RAISES     -> create REFUSES before teardown
```

`_normalize_port` rejects all three of the dropped values when it is allowed to see them:

```
_normalize_port(0)    -> Port out of range for chatbot (services.chat_app.port): 0
_normalize_port('')   -> Invalid port value '' for chatbot (services.chat_app.port)
_normalize_port(None) -> Invalid port value 'None' for chatbot (services.chat_app.port)
```

Those messages are the proof of intent: the values are invalid, and the truthiness filter
upstream is the only reason the check never fires. `'notaport'` is the control — it is
truthy, so it survives extraction and the deployment is correctly refused.

Consequence: with `port: 0` configured, `archi create --force` passes the pre-teardown port
check added by fasrc/archi#293 (`src/cli/cli_main.py:261-266`, merged as PR #299), removes
the running deployment at `:277`, and only then fails inside
`prepare_deployment_files()`. That is exactly the outage shape #293 and fasrc/archi#287 set
out to prevent. Fixes fasrc/archi#311.

**Not a regression from PR #299.** That PR lifted these functions to module level; the
guards are byte-identical to what they replaced. The adversarial review pass on PR #299
found this and deferred it, because #293's scope was teardown *ordering*, and PR #299
carries two characterization tests written to prove the lift changed nothing:
`test_extract_port_config_falsy_zero_dropped` and
`test_extract_port_config_falsy_empty_string_dropped`, both docstring'd "lifted verbatim: no
entry, no error". Closing this gap rewrites those tests, which is a behaviour change and
belongs in its own PR — this one.

## What Changes

- **Extraction keys off configuration, not truthiness.** A module-level `_UNSET` sentinel
  distinguishes "the config did not say" from "the config said something invalid". A value
  the config supplied survives extraction even when it is `0`, `""`, or `None`, and reaches
  `_normalize_port()`, which refuses it before the teardown.
- **An absent section still falls back to the registry defaults**, and an absent `port` key
  inside a present section does too. The distinction the issue asks us to encode is
  "configured as falsy" versus "not configured at all"; we read an explicit `port: null` as
  configured — a configuration error — and a missing key as not configured. Encoded with
  key presence (`"port" in config_value`), not `.get(..., default)`, because `.get` with a
  default cannot tell those two apart.
- **Extraction still drops "no port at all."** Five enabled-capable services — `postgres`,
  `piazza`, `mattermost`, `redmine-mailer`, `benchmarking` — carry
  `default_host_port = None` and no `port_config_path` (`src/cli/service_registry.py:34`).
  The old truthiness guard was load-bearing for them, and not for the reason it looks:
  emitting `postgres_port_host = None` would make the new key-presence test in
  `validate_port_config` normalize it and raise `Invalid port value 'None' for postgres` on
  **every host-mode create**, because postgres is auto-enabled. Measured on `2c404822`. The
  new guard therefore emits a key only when the config supplied a value **or** the registry
  default is not `None`.
- **`validate_port_config` tests key presence** instead of `.get(...) is None`, so a
  configured `None` is validated while an absent key is still skipped.
- **The configured container-side value is range-checked too, and never enters duplicate
  detection.** In non-host mode `port` *is* the container side, so without this the
  acceptance criterion "a service configured `port: 0` fails before the teardown" would hold
  only under `--hostmode`. Container ports must stay out of the duplicate map: `chatbot`
  and `grader` legitimately share container `7861` today (`:109-110`, `:138-139`, measured),
  so duplicate-checking them would refuse the default registry. See design.md D2.
- **The rejected value can never reach the socket probe.** `_normalize_port` raises inside
  `validate_port_config` (`:900`), which returns before the probe loop at `:908`, so the
  issue's constraint is satisfied structurally rather than by a second guard. design.md D3.
- **Behaviour change, deliberately.** A config that was silently accepted at the port check
  and failed later is now refused at the port check. No valid deployment changes: every
  value that starts surviving extraction is a value `_normalize_port` rejects.

## Out of scope, deliberately

- **Host mode deriving the validated port from `external_port`** is fasrc/archi#310, open as
  PR #316 against the same file and the same functions. This change branches from
  `origin/dev` per the issue's constraints, keys off `is not None` exactly as #310 does, and
  touches no line #316 rewrites for a different reason. Whichever merges second rebases.
- **`show_service_urls()`** (`src/cli/utils/helpers.py:412`) reimplements the walk on the
  *display* path. That is fasrc/archi#300 and #293's spec excludes it by name.
- **`_apply_host_mode_port_overrides()`** (`:936-948`) is untouched — it is the render side,
  and it already keys off `is not None`.
- **Teardown ordering itself** (#293, #294). No call site moves.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `cli-create-preflight`: adds a requirement that a port the operator configured is
  validated rather than discarded, so the pre-teardown check cannot pass a config it is
  going to reject later. Recorded as an **ADDED** requirement, not MODIFIED: the change
  `fix-issue-293-validate-ports-before-teardown` is merged but **not yet archived**, so its
  port requirements are not in `openspec/specs/cli-create-preflight/spec.md` and cannot be
  modified. The requirement that *is* in `specs/` today
  ("No destructive step precedes a step that can refuse the deployment", from
  `fix-issue-287-validate-before-teardown`) names this gap in its own honesty note.

## Impact

- `src/cli/managers/templates_manager.py` — a module-level `_UNSET` sentinel; the dict
  branch of `_resolve_ports_from_config()` (`:198-204`) reads by key presence; the emission
  guards in `extract_port_config()` (`:235-238`); the skip test in `validate_port_config()`
  (`:260-262`); a container-side validity check. `_apply_host_mode_port_overrides()` and
  `_normalize_port()` are **not** touched. The file is black-clean under black 24.10.0
  (verified), so the in-place edit reflows no unrelated line.
- `tests/unit/test_templates_port_checks.py` — the two characterization tests named above
  are renamed and re-asserted; `test_validate_port_config_port_zero_raises_when_reached` is
  re-asserted; new cases for `""`, explicit `null`, absent key, absent section, the scalar
  route, and the container side.
- `tests/unit/test_cli_create_dev_smoke.py` — one end-to-end test proving the existing
  deployment survives `port: 0` under `--force`.
- No change to `src/cli/cli_main.py`, the compose templates, the config schema,
  dependencies, or any deployment. No container rebuild.
