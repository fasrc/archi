# Design — fix-issue-310-host-mode-external-port

Line anchors are as of `origin/dev` at `2c404822` (the base of this branch). Re-derive
before citing them in the PR body.

## Facts established by reading the tree

1. `_service_port_config_hint(service_def, host_mode)` — `templates_manager.py:182-186`.
   Returns `f"{service_def.port_config_path}.{suffix}"` with
   `suffix = "port" if host_mode else "external_port"`. It receives no config value, so
   today it cannot know whether `external_port` is present.
2. `_resolve_ports_from_config(...)` — `:189-206`. Module-level since PR #299. In the dict
   branch: `container_port = config_value.get("port", container_port)` then
   `host_port = container_port if host_mode else config_value.get("external_port", host_port)`.
   The non-dict branch assigns `host_port = config_value` and leaves `container_port` at the
   registry default.
3. `extract_port_config(plan, config_manager)` — `:210-239`. Walks
   `service_def.port_config_path` (dotted, e.g. `services.chat_app`) through
   `base_config`, swallowing `KeyError`/`TypeError` to fall back to registry defaults, then
   calls `_resolve_ports_from_config`. Emits `<prefix>_port_host` / `<prefix>_port_container`
   only when the value is truthy (`if host_port:` / `if container_port:`) — that falsy drop
   is fasrc/archi#311 and is out of scope here.
4. `validate_port_config(plan, config_manager, port_config)` — `:242-...`. Reads
   `port_config`, calls `_normalize_port` and `_service_port_config_hint`. `base_config` and
   `services_cfg` are already in scope, so feeding the hint a walked config value needs no
   new plumbing into the function, only inside it.
5. `TemplateManager._apply_host_mode_port_overrides(config)` — `:936-948`, still a method,
   called from `:641` during config preparation. Iterates `config["services"].values()` and
   sets `service_cfg["port"] = external` when `service_cfg.get("external_port") is not None`.
   Guards non-dict service configs with `continue`.
6. Neither `_resolve_ports_from_config` nor `_service_port_config_hint` has any caller
   outside `templates_manager.py` (verified by grep over `src/` and `tests/`). Blast radius
   is this module plus its unit tests.
7. `<prefix>_port_host` / `<prefix>_port_container` render into `base-compose.yaml` as
   `ports: - {{ host }}:{{ container }}` (`:82`, `:262`, `:303`, `:369`). In host mode the
   same services also get `network_mode: host` (`:265` and siblings), which makes the
   `ports:` mapping inert — the process binds the port it reads from its own config, which
   is the `port` key `_apply_host_mode_port_overrides` has already rewritten.
8. `tests/unit/test_templates_port_checks.py:78` is
   `test_extract_port_config_host_mode_uses_port_for_both`, asserting host == container ==
   `port` (8000) for `{"port": 8000, "external_port": 9000}` in host mode. It pins the
   behaviour this change reverses.
9. The only hint assertion in the suite is `:158`, `assert "services.chat_app" in msg` —
   a prefix check. It does not pin the suffix, so changing the host-mode suffix does not
   break it.
10. `templates_manager.py` and `test_templates_port_checks.py` are both black-clean under
    black 24.10.0 (`black --check` reports both unchanged). In-place edits will not reflow
    unrelated lines, so diff coverage will reflect only the lines this change touches.

## Decisions

### D1. Mirror `_apply_host_mode_port_overrides` exactly, including `is not None`

The host-mode branch becomes: prefer `external_port` when it `is not None`, else `port`.
Not `or`, not `.get("external_port", port)`, and not a truthiness test. `external_port: 0`
must land in the derivation the same way it lands in the override — as a present value —
even though `extract_port_config`'s truthy guard then drops it (fact 3, issue #311).
Anything else re-creates the divergence this change exists to remove, just at a different
input.

The rule to hold onto: **the derivation must produce what the override produces, for every
input, including the ones a later guard discards.** Agreement at the interesting values is
what made the current code look correct.

### D2. Host mode derives both ports from the same value

In host mode host and container are one port (fact 7): the mapping is inert and the
override rewrites `port` itself, so a derivation running after the override would read
`9000` for both. Deriving only the host port would leave `9000:7861` in a rendered file
where the pair is meaningless and would break the host-mode invariant that fact 8's test
name asserts, without any consumer wanting the split.

Alternative considered and rejected: change `host_port` only, leaving `container_port` at
`port`. It satisfies the issue's acceptance criteria as literally written (they pin only the
host port in host mode) and is a one-line diff. Rejected because it makes the rendered
compose mapping self-inconsistent and encodes host != container in the one mode where they
cannot differ. Recorded here so a reviewer sees the choice was deliberate.

### D3. The hint names the validated field, via one shared walk

AC5 requires the host-mode hint to name the field actually validated, which is
`external_port` when present and `port` otherwise. The hint cannot tell without the config
value (fact 1). Rather than walk `port_config_path` a second time inside
`validate_port_config`, extract the walk from `extract_port_config` into a module-level
helper and call it from both places.

This keeps faith with #293's standing requirement that the `port_config_path` walk exists
exactly once on the validation path. Adding a second inline walk here would satisfy this
issue and violate that one.

The helper returns the walked value or `None`, absorbing `KeyError`/`TypeError` exactly as
the inline `try`/`except` does today (fact 3) — a fallback that starts raising is a
behaviour change nobody asked for.

### D4. Non-host mode is untouched

`{"port": 8000, "external_port": 9000}`, `host_mode=False` must still yield host `9000`,
container `8000`. The change edits only the `if host_mode` side of the fork. The non-host
characterization test at `:69` must pass unmodified — if it needs editing, the change is
wrong.

### D5. Scope fences that must stay standing

- `show_service_urls()` is fasrc/archi#300. Do not touch `src/cli/utils/helpers.py`.
- The falsy-port drop is fasrc/archi#311.
  `test_validate_port_config_port_zero_raises_when_reached` (`:174`) must still pass,
  unedited. If it goes red, the change has crossed into #311's scope — stop and reduce it.
- `_apply_host_mode_port_overrides` is the correct side of the disagreement. Editing it
  would make the derivation and the render agree on the wrong value.

## Risks

- **Fallout in host-mode consumers.** Anything reading `<prefix>_port_container` in host
  mode now sees `external_port`. Fact 7 says the only consumer is an inert compose mapping,
  but the full unit suite is the check, not the grep — run it and read the failures rather
  than assuming.
- **A newly-detected real duplicate.** A host-mode config with two enabled services sharing
  an `external_port` now fails validation where it previously passed. That is the fix
  working: the deployment could never have bound both. Worth one line in the PR body so a
  reviewer expects it.
