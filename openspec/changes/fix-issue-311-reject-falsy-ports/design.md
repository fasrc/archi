# Design - fix-issue-311-reject-falsy-ports

Line anchors are as of `origin/dev` at `2c404822`. Re-derive before citing any of them.

## The shape of the defect

Three things have to agree and currently two of them disagree with the third:

| Stage | Location | Behaviour on `port: 0` |
|---|---|---|
| extraction | `templates_manager.py:235` | drops the key (falsy) |
| validation | `templates_manager.py:261` | skips the service (key absent) |
| normalization | `templates_manager.py:168-179` | **rejects the value** |

Normalization is the one that encodes the intent. Extraction and validation are what stop it
from ever running. So the fix belongs in extraction and validation, and `_normalize_port`
needs no change at all.

## D1 — A sentinel, because `.get(key, default)` cannot tell "absent" from "null"

The issue asks us to decide what `None` means, and notes it arrives from two routes. It
arrives from three:

1. **Section absent** — the walk at `:220-225` raises `KeyError`, `except` at `:232` passes,
   and both ports keep the registry defaults. This route never produces `None` unless the
   registry default *is* `None`.
2. **Section present, `port` key absent** — `config_value.get("port", container_port)`
   returns the default. Not configured.
3. **Section present, `port: null`** — `.get` returns `None`. Configured, and invalid.

Routes 2 and 3 are indistinguishable through `.get(..., default)`, which is why the current
code cannot act on either. Key presence separates them:

```python
if "port" in config_value:
    container_port = config_value["port"]      # configured, even if None / 0 / ""
```

**Decision:** an explicit `port: null` in YAML is a configuration error. An absent key, or an
absent section, is not. This is the reading the issue calls safest, and it is the only one
that keeps a partially-specified config working.

`_UNSET = object()` at module level carries "the config did not say" out of
`_resolve_ports_from_config` so the caller can apply the registry default itself. A
`None`-valued default is not a substitute for the sentinel — that is the exact conflation
that produced the bug.

## D2 — The container side is range-checked, and never duplicate-checked

**Why check it at all.** Acceptance criterion 1 says a service configured `port: 0` must fail
before the teardown, and does not say "under `--hostmode`". In non-host mode `port` is the
*container* side (`:199`); the host side comes from `external_port` or the registry default.
So a host-side-only fix satisfies the criterion only in host mode. Measured on `2c404822`,
non-host, `{"port": 0}` resolves to `host=7861, container=0` — a fix that watches only the
host side lets that config through.

**Why not duplicate-check it.** Container ports live in separate network namespaces, so
sharing one is legal, and the registry already does:

```
container 7861: chatbot(host=7861), grader(host=7862)   <-- SHARED
```

Adding container ports to `port_to_services` would make `validate_port_config` report
"Port 7861 is assigned to multiple services" for the **default** registry whenever chatbot
and grader are both enabled. That is a refusal of a working deployment, which is worse than
the bug being fixed.

**Decision:** validate the configured container value with `_normalize_port` for validity
only — same call, same message, same `config_hint` — and do not append it to `port_usages`.
Validate it only when it was configured, so a registry default is never re-checked and no
existing deployment changes verdict.

## D3 — Nothing new reaches the socket probe

The issue's constraint: "A value that starts surviving extraction will now also be
socket-probed. Confirm that is acceptable for every value you let through, or reject it
before it reaches the probe."

It is rejected before the probe, structurally, with no second guard:

```
_check_ports_available            (:893)
  -> validate_port_config         (:900)   <- _normalize_port raises here
  -> if not allow_port_reuse:     (:908)   <- probe loop, never entered
       self._probe_port(port)     (:915)
```

`_normalize_port` raises `ValueError` for every value this change newly preserves — `0`,
`""`, `None` — and the exception propagates out of `_check_ports_available` uncaught. Only
values that normalize to `1..65535` ever reach `port_to_services`, and those are exactly the
values that reach the probe today. The set of probed values is therefore unchanged.

The same holds on the pre-teardown path: `cli_main.py:261-266` calls `validate_port_config`
directly and never probes.

## D4 — Extraction must keep dropping "no port at all"

This is the trap that makes the obvious fix wrong. Replace the truthiness guards with a bare
emit and 10 keys appear that are not there today:

```
benchmarking_port_{host,container} = None
mattermost_port_{host,container}   = None
piazza_port_{host,container}        = None
postgres_port_{host,container}      = None
redmine_mailer_port_{host,container} = None
```

Those five services have `default_host_port = None` and no `port_config_path`
(`src/cli/service_registry.py:34`). They render nothing — the compose template references
only `data_manager`, `chatbot`, `grafana`, `grader`
(`src/cli/templates/base-compose.yaml:82,262,303,369`) — so the damage is not in rendering.
It is in validation. Combine the bare emit with D5's key-presence test and:

```
postgres: Invalid port value 'None' for postgres
```

on **every host-mode create**, because `postgres` is auto-enabled (measured: enabled services
for `--services chatbot --hostmode` are `['data-manager', 'postgres', 'chatbot']`).

**Decision:** emit a key when the config supplied a value **or** the registry default is not
`None`. Written as one condition per side, next to the guard it replaces, with a comment
naming postgres — the next reader will otherwise "simplify" it straight back into the bug.

## D5 — `validate_port_config` tests key presence, not `is None`

`port_config.get(f"{key_prefix}_port_host")` returns `None` for both "absent" and "present
and `None`", so the `is None` skip at `:261` would discard the very value D1 works to
preserve. `if key not in port_config: continue` separates them, and D4 guarantees the only
`None` that can be present is a configured one.

## D6 — Scope fences

Stop and reduce the change if you find yourself editing:

- `_apply_host_mode_port_overrides` (`:936-948`) — the render side, already correct, and the
  subject of fasrc/archi#310 / PR #316.
- `src/cli/utils/helpers.py` — the display path, fasrc/archi#300.
- `_normalize_port` (`:168-179`) — its rejections are the specification being restored; a
  change here means the fix went in the wrong place.
- Any call site in `src/cli/cli_main.py` — teardown ordering is #293 and #294.
- `test_extract_port_config_host_mode_no_external_port_uses_port_for_both` (`:78`) — host-mode derivation is
  #310's business. This change must leave it passing untouched.

## Alternatives rejected

- **Validate inside `extract_port_config`.** Extraction is called on the render path
  (`:857`) as well as the validation path, and #293's standing requirement is one derivation
  shared by both. Raising during extraction would move the refusal earlier for some callers
  and change what `restart --allow-port-reuse` does. Rejected: the layer that already owns
  refusal is `validate_port_config`.
- **Coerce falsy ports to the registry default.** Silently deploying on 7861 when the
  operator wrote `port: 0` is the same class of bug as the one being fixed — a config that
  was going to be refused is instead accepted with different behaviour. Rejected.
- **Keep the truthiness guard and special-case `0` and `""`.** Enumerates the falsy set
  instead of asking the real question (did the config say?), and misses `None`. Rejected;
  three review rounds on #293 are the standing evidence that enumeration does not converge.
