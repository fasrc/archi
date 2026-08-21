"""The CLI reference must document the port values ``archi create`` now refuses.

Issue #311 changed a silent fallback into a refusal. Before it, ``port: 0``, ``port: ""`` and
``port: null`` were dropped during port extraction, preflight passed, ``create --force`` tore
down the running deployment, and the run then failed at render time -- an outage caused by a
typo. Now the configured value is preserved and validated, and the run is refused *before* the
teardown.

That is operator-visible in the only way that matters: a `create` that used to appear to work
now stops with a message. Documenting it only in the OpenSpec change leaves the operator reading
the published reference with no explanation (Greptile review, PR #317).

The refusal list is a promise, and prose has no compiler. This test derives the list from the
real ``validate_port_config`` -- every literal the doc names must actually be refused, and every
literal the code refuses must be named -- so the table cannot drift in either direction. It does
not pin wording.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.cli.managers.templates_manager import extract_port_config, validate_port_config
from src.cli.utils.service_builder import ServiceBuilder

CLI_REFERENCE = (
    Path(__file__).resolve().parents[2] / "docs" / "docs" / "cli_reference.md"
)

_SECTION_HEADING = "### Rejected port values"

# A port literal as written in YAML -> the Python value the loader produces. This is the
# COMPLETE set of falsy scalars a port key can carry, which is what lets the parity check
# below treat "the code refuses it" as the whole truth rather than a sample.
_YAML_LITERALS = {"0": 0, '""': "", "null": None}

# The doc must say an omitted key is not the same as an explicit one, and that omission keeps
# the default. Which words it uses is the writer's business.
_OMISSION_RULE = re.compile(r"omit", re.IGNORECASE)
_DEFAULT_RULE = re.compile(r"default", re.IGNORECASE)


def _section() -> str:
    """The refusal section, from its heading to the next heading of any depth."""
    text = CLI_REFERENCE.read_text(encoding="utf-8")
    assert _SECTION_HEADING in text, (
        f"{CLI_REFERENCE.name} has no '{_SECTION_HEADING}' section; the refusal contract "
        "operators actually hit is undocumented."
    )
    rest = text[text.index(_SECTION_HEADING) + len(_SECTION_HEADING) :]
    next_heading = re.search(r"^#{1,6} ", rest, re.MULTILINE)
    return rest[: next_heading.start()] if next_heading else rest


def _verdict(port_value, *, host_mode):
    """Run the real preflight for one configured ``port`` value; return the message or None."""
    plan = ServiceBuilder.build_compose_config(
        name="docs-contract",
        verbosity=0,
        base_dir=Path("/tmp/docs-contract"),
        enabled_services=["chatbot"],
        host_mode=host_mode,
    )
    base = {"services": {"chat_app": {"port": port_value}}}
    cm = SimpleNamespace(get_configs=lambda: [base])
    try:
        port_config = extract_port_config(plan, cm)
        validate_port_config(plan, cm, port_config)
    except ValueError as exc:
        return str(exc)
    return None


@pytest.mark.parametrize("host_mode", [True, False])
@pytest.mark.parametrize("literal,value", sorted(_YAML_LITERALS.items()))
def test_every_documented_literal_is_actually_refused(literal, value, host_mode):
    # Guards the doc against over-promising: a literal listed as refused must really raise,
    # in both deployment modes.
    assert _verdict(value, host_mode=host_mode) is not None, (
        f"the reference lists `port: {literal}` as refused, but preflight accepts it "
        f"(host_mode={host_mode})"
    )


def test_the_section_names_every_literal_the_code_refuses():
    # Guards the doc against under-promising: an operator who hits a refusal must find the
    # value they wrote in this table.
    section = _section()
    missing = [
        literal
        for literal, value in _YAML_LITERALS.items()
        if _verdict(value, host_mode=False) is not None and literal not in section
    ]
    assert not missing, f"preflight refuses {missing}, but the section never names them"


def test_the_section_states_that_omitting_a_port_is_not_an_error():
    # The distinction #311 encodes: an omitted key falls back to the registry default; only a
    # value the operator wrote is validated. Without this an operator reads the refusal list
    # and concludes every service needs an explicit port.
    section = _section()
    assert _OMISSION_RULE.search(
        section
    ), "the section never says an omitted port key is left alone"
    assert _DEFAULT_RULE.search(
        section
    ), "the section never says an omitted port key keeps its default"
    plan_default = _verdict(7861, host_mode=False)
    assert plan_default is None, "code changed; update this doc test"


def test_the_section_says_the_refusal_precedes_the_teardown():
    # The ordering is the point of the fix (fasrc/archi#287, #311): a refusable config must not
    # cost the operator a running deployment first.
    section = _section().lower()
    assert "before" in section, "the section never states the refusal ordering"
    assert (
        "--force" in section or "teardown" in section or "tear down" in section
    ), "the section never says what the refusal happens before"
