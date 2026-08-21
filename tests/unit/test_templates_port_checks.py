"""Unit tests for the module-level port-check functions in templates_manager.

These tests target extract_port_config and validate_port_config (to be lifted
to module level in task 1.2), covering the scenarios enumerated in task 1.1.
Delegator tests for _check_ports_available live in this same file (task 1.3).
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.cli.managers.templates_manager import (
    TemplateContext,
    TemplateManager,
    _resolve_ports_from_config,
    extract_port_config,
    validate_port_config,
)
from src.cli.utils.service_builder import ServiceBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan(enabled_services, *, host_mode=False):
    """Minimal DeploymentPlan for port-check tests."""
    return ServiceBuilder.build_compose_config(
        name="test",
        verbosity=0,
        base_dir=Path("/tmp/test"),
        enabled_services=enabled_services,
        host_mode=host_mode,
    )


def _cm(services_cfg=None, *, postgres_port=5432):
    """Fake config manager wrapping a services dict."""
    cfg = {"services": services_cfg or {}}
    if postgres_port != 5432:
        svc = cfg.setdefault("services", {})
        svc.setdefault("postgres", {})["port"] = postgres_port
    return SimpleNamespace(get_configs=lambda: [cfg])


# ---------------------------------------------------------------------------
# extract_port_config — valid multi-service plan returns expected keys
# ---------------------------------------------------------------------------


def test_extract_port_config_valid_multi_service():
    plan = _plan(["chatbot", "data-manager"])
    cm = _cm()
    result = extract_port_config(plan, cm)
    # chatbot defaults: host=7861, container=7861
    assert result["chatbot_port_host"] == 7861
    assert result["chatbot_port_container"] == 7861
    # data-manager defaults: host=7871, container=7871
    assert result["data_manager_port_host"] == 7871
    assert result["data_manager_port_container"] == 7871


# ---------------------------------------------------------------------------
# extract_port_config — host_mode=True vs host_mode=False (dict branch)
# ---------------------------------------------------------------------------


def test_extract_port_config_non_host_mode_uses_external_port():
    # In non-host mode the host port comes from external_port; container from port.
    plan = _plan(["chatbot"], host_mode=False)
    cm = _cm({"chat_app": {"port": 8000, "external_port": 9000}})
    result = extract_port_config(plan, cm)
    assert result["chatbot_port_host"] == 9000
    assert result["chatbot_port_container"] == 8000


def test_extract_port_config_host_mode_uses_external_port_for_both():
    # In host mode both ports come from external_port when present; host mirrors the override.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": 7861, "external_port": 9000}})
    result = extract_port_config(plan, cm)
    assert result["chatbot_port_host"] == 9000
    assert result["chatbot_port_container"] == 9000


def test_extract_port_config_host_mode_no_external_port_uses_port_for_both():
    # In host mode with no external_port, both ports come from port (AC3, no regression).
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": 7861}})
    result = extract_port_config(plan, cm)
    assert result["chatbot_port_host"] == 7861
    assert result["chatbot_port_container"] == 7861


def test_extract_port_config_host_mode_external_port_only_used_for_both():
    # In host mode with external_port present and port absent, both come from external_port.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"external_port": 9000}})
    result = extract_port_config(plan, cm)
    assert result["chatbot_port_host"] == 9000
    assert result["chatbot_port_container"] == 9000


def test_resolve_ports_from_config_host_mode_external_port_zero_is_present():
    # external_port: 0 is treated as present (D1) — derivation returns it. The truthy
    # guard in extract_port_config that used to drop it afterwards is gone (#311), so
    # the value now survives to validation; see
    # test_validate_port_config_host_mode_falsy_external_port_raises.
    host, container = _resolve_ports_from_config(
        {"port": 7861, "external_port": 0},
        host_mode=True,
        host_default=7861,
        container_default=7861,
    )
    assert host == 0
    assert container == 0


# ---------------------------------------------------------------------------
# validate_port_config — host-mode duplicate external_port returns error (AC2)
# ---------------------------------------------------------------------------


def test_validate_port_config_host_mode_duplicate_external_port_returns_error():
    # Two enabled services with the same external_port in host mode must conflict.
    plan = _plan(["chatbot", "data-manager"], host_mode=True)
    cm = _cm(
        {
            "chat_app": {"port": 7861, "external_port": 9000},
            "data_manager": {"port": 7871, "external_port": 9000},
        }
    )
    port_config = extract_port_config(plan, cm)
    _, errors = validate_port_config(plan, cm, port_config)
    assert len(errors) == 1
    assert "assigned to multiple services" in errors[0]
    # The conflict message must name the key that produced the colliding value, not the
    # inert `port` key underneath it — the duplicate path reads the same config hint as the
    # invalid-value path, and nothing pinned that (Greptile review, PR #316).
    assert "services.chat_app.external_port" in errors[0]
    assert "services.data_manager.external_port" in errors[0]


# ---------------------------------------------------------------------------
# extract_port_config — scalar config value branch
# ---------------------------------------------------------------------------


def test_extract_port_config_scalar_config_value():
    # When the config value is a scalar (not a dict), it becomes the host port.
    plan = _plan(["chatbot"])
    cm = _cm({"chat_app": 9999})
    result = extract_port_config(plan, cm)
    assert result["chatbot_port_host"] == 9999


# ---------------------------------------------------------------------------
# extract_port_config — registry-defaults fallback when config omits section
# ---------------------------------------------------------------------------


def test_extract_port_config_falls_back_to_registry_defaults():
    plan = _plan(["chatbot"])
    cm = _cm({})  # no chat_app section → use registry defaults
    result = extract_port_config(plan, cm)
    assert result["chatbot_port_host"] == 7861
    assert result["chatbot_port_container"] == 7861


# ---------------------------------------------------------------------------
# extract_port_config — falsy values (0, "") are preserved, not discarded
# ---------------------------------------------------------------------------


def test_extract_port_config_falsy_zero_preserved():
    # A configured port of 0 is preserved and later validated, not silently dropped.
    plan = _plan(["chatbot"])
    cm = _cm({"chat_app": 0})
    result = extract_port_config(plan, cm)
    assert "chatbot_port_host" in result
    assert result["chatbot_port_host"] == 0


def test_extract_port_config_falsy_empty_string_preserved():
    # An empty string is preserved and later validated (AC2).
    plan = _plan(["chatbot"])
    cm = _cm({"chat_app": ""})
    result = extract_port_config(plan, cm)
    assert "chatbot_port_host" in result
    assert result["chatbot_port_host"] == ""


# ---------------------------------------------------------------------------
# validate_port_config — nonnumeric host-side port raises ValueError
# ---------------------------------------------------------------------------


def test_validate_port_config_nonnumeric_host_port_raises():
    plan = _plan(["chatbot"], host_mode=True)
    # Scalar "abc" reaches _normalize_port via extract_port_config, which raises.
    cm = _cm({"chat_app": "abc"})
    port_config = extract_port_config(plan, cm)
    # The bad value lands in port_config; validate_port_config must raise.
    with pytest.raises(ValueError, match="abc"):
        validate_port_config(plan, cm, port_config)


def test_validate_port_config_nonnumeric_names_service_and_hint():
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": "notaport"})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "chatbot" in msg
    # The config hint names the path (services.chat_app.port in host mode).
    assert "services.chat_app" in msg


def test_validate_port_config_host_mode_with_external_port_names_external_port():
    # AC5: host mode, external_port present and invalid → hint suffix is external_port.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": 7861, "external_port": "notaport"}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "services.chat_app.external_port" in msg


def test_validate_port_config_host_mode_without_external_port_names_port():
    # AC5: host mode, no external_port, port invalid → hint suffix is port.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": "notaport"}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "services.chat_app.port" in msg


# ---------------------------------------------------------------------------
# validate_port_config — YAML booleans are not ports (Codex review, PR #317)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host_mode", [True, False])
def test_validate_port_config_boolean_port_is_refused(host_mode):
    # PyYAML resolves `on`, `yes` and `true` to True, so `port: on` is a realistic typo.
    # int(True) is 1, so without an explicit bool guard preflight accepts it as port 1 —
    # and port_config carries the bool itself into template rendering.
    plan = _plan(["chatbot"], host_mode=host_mode)
    cm = _cm({"chat_app": {"port": True}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "services.chat_app.port" in msg


def test_validate_port_config_boolean_external_port_is_refused():
    # Container mode: external_port supplies the host-side port, so the same guard applies.
    plan = _plan(["chatbot"], host_mode=False)
    cm = _cm({"chat_app": {"port": 7861, "external_port": True}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "services.chat_app.external_port" in msg


def test_validate_port_config_boolean_false_is_refused_as_a_bool_not_as_zero():
    # `port: off` already raised, but only because int(False) == 0 tripped the range check.
    # The message must identify it as an invalid value, not report a port number of 0.
    plan = _plan(["chatbot"], host_mode=False)
    cm = _cm({"chat_app": {"port": False}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "Invalid port value" in msg
    assert "out of range" not in msg


# ---------------------------------------------------------------------------
# validate_port_config — out-of-range host-side port raises ValueError
# ---------------------------------------------------------------------------


def test_validate_port_config_port_too_high_raises():
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": 99999})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError, match="99999"):
        validate_port_config(plan, cm, port_config)


def test_validate_port_config_port_zero_raises_when_reached():
    # Port 0 is preserved by extract_port_config and validated; it is out of range.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": 0})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError):
        validate_port_config(plan, cm, port_config)


# ---------------------------------------------------------------------------
# validate_port_config — duplicate host ports return errors, do NOT raise
# ---------------------------------------------------------------------------


def test_validate_port_config_duplicate_port_returns_error_string():
    # chatbot default host port in non-host mode comes from external_port (or default 7861).
    # Give chatbot and data-manager the same external_port.
    plan = _plan(["chatbot", "data-manager"], host_mode=False)
    cm = _cm(
        {
            "chat_app": {"port": 7861, "external_port": 7861},
            "data_manager": {"port": 7861, "external_port": 7861},
        }
    )
    port_config = extract_port_config(plan, cm)
    _, errors = validate_port_config(plan, cm, port_config)
    assert len(errors) == 1
    assert "7861" in errors[0]
    assert "multiple services" in errors[0]


def test_validate_port_config_duplicate_does_not_raise():
    plan = _plan(["chatbot", "data-manager"], host_mode=False)
    cm = _cm(
        {
            "chat_app": {"port": 7861, "external_port": 7861},
            "data_manager": {"port": 7861, "external_port": 7861},
        }
    )
    port_config = extract_port_config(plan, cm)
    # Must not raise — caller raises on non-empty list.
    result = validate_port_config(plan, cm, port_config)
    assert isinstance(result, tuple)
    port_to_services, errors = result
    assert isinstance(errors, list)


# ---------------------------------------------------------------------------
# validate_port_config — DISABLED service with bad/conflicting port: NO error
# ---------------------------------------------------------------------------


def test_validate_port_config_disabled_service_invalid_port_ignored():
    # chatbot is enabled; grader is NOT enabled.
    # Give grader the same port as chatbot — the validator must not flag it.
    plan = _plan(["chatbot"], host_mode=False)
    cm = _cm(
        {
            "chat_app": {"port": 7861, "external_port": 7861},
            # grader_app deliberately shares the same external_port.
            "grader_app": {"port": 7861, "external_port": 7861},
        }
    )
    port_config = extract_port_config(plan, cm)
    _, errors = validate_port_config(plan, cm, port_config)
    assert errors == []


def test_validate_port_config_disabled_service_nonnumeric_port_ignored():
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm(
        {
            "chat_app": {"port": 7861},
            # grader_app has a bad port but is disabled.
            "grader_app": "bad",
        }
    )
    port_config = extract_port_config(plan, cm)
    # No exception expected because grader is not in enabled_services.
    _, errors = validate_port_config(plan, cm, port_config)
    assert errors == []


# ---------------------------------------------------------------------------
# validate_port_config — host-mode postgres entry from services.postgres.port
# ---------------------------------------------------------------------------


def test_validate_port_config_host_mode_includes_postgres_port():
    plan = _plan(["chatbot"], host_mode=True)
    # postgres is auto-enabled; give it an explicit port.
    cm_cfg = {
        "chat_app": {"port": 7861},
        "postgres": {"port": 5432},
    }
    cm = _cm(cm_cfg)
    port_config = extract_port_config(plan, cm)
    port_to_services, errors = validate_port_config(plan, cm, port_config)
    # postgres port must appear in the mapping.
    assert 5432 in port_to_services
    assert any(s == "postgres" for s, _ in port_to_services[5432])


def test_validate_port_config_host_mode_postgres_conflict_returns_error():
    # chatbot and postgres share the same port → duplicate error.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": 5432}, "postgres": {"port": 5432}})
    port_config = extract_port_config(plan, cm)
    _, errors = validate_port_config(plan, cm, port_config)
    assert any("5432" in e and "multiple services" in e for e in errors)


def test_validate_port_config_non_host_mode_postgres_not_included():
    # In non-host mode the postgres port is not added to port_usages.
    plan = _plan(["chatbot"], host_mode=False)
    cm = _cm({"postgres": {"port": 5432}})
    port_config = extract_port_config(plan, cm)
    port_to_services, _ = validate_port_config(plan, cm, port_config)
    assert 5432 not in port_to_services


# ---------------------------------------------------------------------------
# validate_port_config — return type is (port_to_services, errors)
# ---------------------------------------------------------------------------


def test_validate_port_config_return_type():
    plan = _plan(["chatbot"])
    cm = _cm()
    port_config = extract_port_config(plan, cm)
    result = validate_port_config(plan, cm, port_config)
    assert isinstance(result, tuple)
    port_to_services, errors = result
    assert isinstance(port_to_services, dict)
    assert isinstance(errors, list)


# ---------------------------------------------------------------------------
# Delegator tests for _check_ports_available (task 1.3)
# _probe_port is monkeypatched — never bind real ports.
# ---------------------------------------------------------------------------


def _tm():
    """Minimal TemplateManager for delegator tests."""
    return TemplateManager(jinja_env=MagicMock(), verbosity=0)


def _ctx(plan, cm, **options):
    """Minimal TemplateContext for delegator tests."""
    return TemplateContext(
        plan=plan,
        config_manager=cm,
        secrets_manager=MagicMock(),
        options=options,
    )


def test_check_ports_available_probe_runs_when_not_allow_reuse(monkeypatch):
    # With allow_port_reuse=False (the default), _probe_port is called for
    # each host port in port_to_services.
    plan = _plan(["chatbot"])
    cm = _cm()
    port_config = extract_port_config(plan, cm)
    tm = _tm()

    probed = []
    monkeypatch.setattr(tm, "_probe_port", lambda port: probed.append(port) or None)

    tm._check_ports_available(_ctx(plan, cm), port_config, allow_port_reuse=False)
    assert len(probed) > 0


def test_check_ports_available_probe_skipped_with_allow_reuse_but_duplicate_raises(
    monkeypatch,
):
    # restart() calls _check_ports_available with allow_port_reuse=True so that
    # a running deployment's own ports are not flagged as "in use".  The probe
    # loop must be skipped, but duplicate-assignment errors (from
    # validate_port_config) must still raise.
    plan = _plan(["chatbot", "data-manager"], host_mode=False)
    cm = _cm(
        {
            "chat_app": {"port": 7861, "external_port": 7861},
            "data_manager": {"port": 7861, "external_port": 7861},
        }
    )
    port_config = extract_port_config(plan, cm)
    tm = _tm()

    probed = []
    monkeypatch.setattr(tm, "_probe_port", lambda port: probed.append(port) or "in use")

    with pytest.raises(ValueError, match="Port check failed"):
        tm._check_ports_available(_ctx(plan, cm), port_config, allow_port_reuse=True)

    assert probed == [], "probe must not be called when allow_port_reuse=True"


def test_check_ports_available_combined_duplicate_and_in_use_message(monkeypatch):
    # When both a duplicate-port error (from validate_port_config) and an
    # in-use error (from _probe_port) are present, both appear in one
    # ValueError whose message starts with "Port check failed:\n".
    plan = _plan(["chatbot", "data-manager"], host_mode=False)
    cm = _cm(
        {
            "chat_app": {"port": 7861, "external_port": 7861},
            "data_manager": {"port": 7861, "external_port": 7861},
        }
    )
    port_config = extract_port_config(plan, cm)
    tm = _tm()

    monkeypatch.setattr(tm, "_probe_port", lambda port: "Address already in use")

    with pytest.raises(ValueError) as exc_info:
        tm._check_ports_available(_ctx(plan, cm), port_config, allow_port_reuse=False)

    msg = str(exc_info.value)
    assert msg.startswith("Port check failed:\n")
    assert "multiple services" in msg  # from validate_port_config duplicate
    assert "already in use" in msg  # from _probe_port


def test_extract_port_config_delegator_returns_same_as_module_function():
    # _extract_port_config(context) delegates to extract_port_config(plan, cm);
    # both must return the same dict.
    plan = _plan(["chatbot"])
    cm = _cm()
    tm = _tm()

    via_delegator = tm._extract_port_config(_ctx(plan, cm))
    via_module = extract_port_config(plan, cm)
    assert via_delegator == via_module


# ---------------------------------------------------------------------------
# extract_port_config — falsy configured values are preserved (AC2, D1)
# ---------------------------------------------------------------------------


def test_extract_port_config_falsy_zero_dict_preserved():
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": 0}})
    result = extract_port_config(plan, cm)
    assert "chatbot_port_host" in result
    assert result["chatbot_port_host"] == 0


def test_extract_port_config_falsy_empty_string_dict_preserved():
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": ""}})
    result = extract_port_config(plan, cm)
    assert "chatbot_port_host" in result
    assert result["chatbot_port_host"] == ""


def test_extract_port_config_falsy_none_dict_preserved():
    # A configured null is distinct from an absent key (design.md D1).
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": None}})
    result = extract_port_config(plan, cm)
    assert "chatbot_port_host" in result
    assert result["chatbot_port_host"] is None


def test_validate_port_config_falsy_zero_raises():
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": 0}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "chatbot" in msg
    assert "services.chat_app" in msg


def test_validate_port_config_falsy_empty_string_raises():
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": ""}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "chatbot" in msg
    assert "services.chat_app" in msg


def test_validate_port_config_falsy_null_raises():
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": None}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "chatbot" in msg
    assert "services.chat_app" in msg


def test_validate_port_config_host_mode_falsy_external_port_raises():
    # The seam between #310/#316 and #311. In host mode external_port is the value
    # the deployment binds, so a falsy external_port is the real outage shape: it was
    # always going to be refused. #316 derives it (present, not None) and #311 stops
    # extraction from dropping it, so validation now refuses it before the teardown.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": 7861, "external_port": 0}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "chatbot" in msg
    assert "services.chat_app.external_port" in msg


def test_validate_port_config_host_mode_valid_external_port_shadows_falsy_port():
    # Accepted limitation, pinned so the precedence rule is visible. In host mode a
    # valid external_port overrides port on the render side too
    # (_apply_host_mode_port_overrides), so a falsy `port` underneath it is dead
    # config: the deployment binds 9000 and succeeds. Nothing is refused, and no
    # deployment is lost -- which is why this is a strictness gap, not the outage
    # #311 fixes. Refusing dead config would need a check on a value no longer in
    # port_config; that is follow-up work, not this merge's.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": 0, "external_port": 9000}})
    port_config = extract_port_config(plan, cm)
    assert port_config["chatbot_port_host"] == 9000
    assert port_config["chatbot_port_container"] == 9000
    _, errors = validate_port_config(plan, cm, port_config)
    assert errors == []


def test_extract_port_config_scalar_zero_preserved():
    # Scalar route: a configured falsy scalar is preserved (AC2).
    plan = _plan(["chatbot"])
    cm = _cm({"chat_app": 0})
    result = extract_port_config(plan, cm)
    assert "chatbot_port_host" in result


def test_extract_port_config_scalar_empty_string_preserved():
    plan = _plan(["chatbot"])
    cm = _cm({"chat_app": ""})
    result = extract_port_config(plan, cm)
    assert "chatbot_port_host" in result


# ---------------------------------------------------------------------------
# Regression guards — must pass both before and after the fix (AC4, D4)
# ---------------------------------------------------------------------------


def test_extract_port_config_no_section_uses_registry_defaults():
    # No chat_app section → registry defaults are used and emitted.
    plan = _plan(["chatbot"])
    cm = _cm({})
    result = extract_port_config(plan, cm)
    assert result["chatbot_port_host"] == 7861
    assert result["chatbot_port_container"] == 7861
    _, errors = validate_port_config(plan, cm, result)
    assert errors == []


def test_extract_port_config_section_no_port_key_uses_registry_defaults():
    # Section present but no port key at all → registry defaults for both sides
    # (D1 route 2: "not configured", distinct from a configured null).
    # The section must carry no port key of any kind: in host mode external_port is
    # itself authoritative (#310/#316), so a section holding only external_port is
    # not the "no port key" case and is pinned by
    # test_extract_port_config_host_mode_external_port_only_used_for_both instead.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"agent_class": "SomeAgent"}})
    result = extract_port_config(plan, cm)
    assert result["chatbot_port_host"] == 7861
    assert result["chatbot_port_container"] == 7861
    _, errors = validate_port_config(plan, cm, result)
    assert errors == []


def test_extract_port_config_postgres_not_emitted_validate_no_postgres_error():
    # Postgres has no port default and no config path; it must not appear in the
    # extraction output.  Replacing the emission guard with a bare emit would add
    # postgres with value None and cause _normalize_port to raise here instead.
    plan = _plan(["chatbot"], host_mode=True)
    cm = _cm({"chat_app": {"port": 7861}})
    result = extract_port_config(plan, cm)
    assert "postgres_port_host" not in result
    _, errors = validate_port_config(plan, cm, result)
    assert not any("postgres" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_port_config — non-host-mode configured container port is range-checked
# (D2: container side validity without duplicate detection)
# ---------------------------------------------------------------------------


def test_validate_port_config_non_host_falsy_zero_raises():
    # In non-host mode, 'port' is the container port. port=0 is out of range
    # and must be refused even though the host port (from registry default) is valid.
    plan = _plan(["chatbot"], host_mode=False)
    cm = _cm({"chat_app": {"port": 0}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "chatbot" in msg
    assert "services.chat_app" in msg


def test_validate_port_config_non_host_falsy_empty_string_raises():
    # Non-host mode container port of "" is non-numeric and must be refused.
    plan = _plan(["chatbot"], host_mode=False)
    cm = _cm({"chat_app": {"port": ""}})
    port_config = extract_port_config(plan, cm)
    with pytest.raises(ValueError) as exc_info:
        validate_port_config(plan, cm, port_config)
    msg = str(exc_info.value)
    assert "chatbot" in msg
    assert "services.chat_app" in msg


# ---------------------------------------------------------------------------
# Fence: chatbot+grader container port sharing is legal (D2, AC6)
# ---------------------------------------------------------------------------


def test_validate_port_config_chatbot_grader_both_enabled_no_duplicate_error():
    # chatbot and grader both default to container port 7861 (separate namespaces).
    # Only host ports are duplicate-checked: chatbot=7861, grader=7862 -> no conflict.
    # If this test ever goes red, container ports were added to duplicate detection.
    plan = _plan(["chatbot", "grader"], host_mode=False)
    cm = _cm({})
    port_config = extract_port_config(plan, cm)
    port_to_services, errors = validate_port_config(plan, cm, port_config)
    assert errors == []
    assert 7861 in port_to_services
    assert len(port_to_services[7861]) == 1
    assert port_to_services[7861][0][0] == "chatbot"
