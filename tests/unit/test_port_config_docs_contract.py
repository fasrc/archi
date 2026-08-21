"""The configuration reference must document the port contract the CLI enforces.

Issue #310 changed two operator-visible things about host mode. The effective host and
container port are now both derived from ``services.<svc>.external_port`` when that key is
present (falling back to ``port``), and the preflight diagnostic names *the key it actually
validated* rather than always naming ``port``. Both are user-facing: an operator whose
``archi create`` is refused reads the message and edits the key it names.

Neither fact was in ``docs/docs/configuration.md``, which listed ``external_port`` only as a
table row reading "Host-mapped port" -- true in non-host mode, and the opposite of what host
mode does with it (Greptile review, PR #316).

This does not pin prose. It reads the key names out of the documented section and compares
them against what the real helpers emit, so the doc cannot claim a key the code does not use
and cannot omit one it does. The behaviour itself is covered from the code end by
``test_templates_port_checks.py``; this file only guards the doc against drifting from it.
"""

import re
from pathlib import Path

from src.cli.managers.templates_manager import (
    _resolve_ports_from_config,
    _service_port_config_hint,
)
from src.cli.service_registry import service_registry

CONFIGURATION_DOC = (
    Path(__file__).resolve().parents[2] / "docs" / "docs" / "configuration.md"
)

# The section is addressed by its heading, not by line number, so re-ordering the reference
# does not break this test.
_SECTION_HEADING = "### Service ports"

_BACKTICKED = re.compile(r"`([^`]+)`")

# "refused", "refuses", "rejected", "rejects" -- the doc must say the run is stopped, not
# merely that the value is "used". Which verb it picks is the writer's business.
_REFUSAL = re.compile(r"refus|reject", re.IGNORECASE)


def _port_section() -> str:
    """The text of the port section, from its heading to the next heading of any depth."""
    text = CONFIGURATION_DOC.read_text(encoding="utf-8")
    assert _SECTION_HEADING in text, (
        f"{CONFIGURATION_DOC.name} has no '{_SECTION_HEADING}' section; the port contract "
        "the CLI enforces is undocumented."
    )
    start = text.index(_SECTION_HEADING)
    rest = text[start + len(_SECTION_HEADING) :]
    next_heading = re.search(r"^#{1,6} ", rest, re.MULTILINE)
    return rest[: next_heading.start()] if next_heading else rest


def _hint_suffixes_the_code_can_emit() -> set:
    """Every ``services.<svc>.<key>`` suffix ``_service_port_config_hint`` can name."""
    service_def = service_registry.get_service("chatbot")
    hints = {
        _service_port_config_hint(
            service_def, True, config_value={"external_port": 9000}
        ),
        _service_port_config_hint(service_def, True, config_value={"port": 7861}),
        _service_port_config_hint(service_def, False),
    }
    return {hint.rsplit(".", 1)[1] for hint in hints if hint}


def test_port_section_documents_every_key_the_diagnostic_can_name():
    # An operator reading a refusal that names one of these keys must find it here.
    section = _port_section()
    documented = set(_BACKTICKED.findall(section))
    missing = _hint_suffixes_the_code_can_emit() - documented
    assert not missing, (
        f"the port section never mentions {sorted(missing)}, but the preflight diagnostic "
        "can name those keys"
    )


def test_port_section_states_the_host_mode_derivation():
    # Host mode with external_port present: BOTH sides come from external_port. The doc must
    # say so, because the per-service table's "Host-mapped port" gloss implies otherwise.
    host_port, container_port = _resolve_ports_from_config(
        {"port": 7861, "external_port": 9000},
        host_mode=True,
        host_default=None,
        container_default=None,
    )
    assert (host_port, container_port) == (
        9000,
        9000,
    ), "code changed; update this doc test"

    section = _port_section()
    assert "host mode" in section.lower(), "the port section never mentions host mode"
    assert "external_port" in section, "the port section never names external_port"


def test_port_section_says_an_invalid_or_duplicate_port_stops_the_run():
    # The refusal happens at create preflight, before any teardown -- that ordering is the
    # whole point of the check, so the doc has to carry it.
    section = _port_section()
    assert _REFUSAL.search(
        section
    ), "the port section never says an invalid or duplicate port is refused"
    assert "create" in section, "the port section never names the command that refuses"
