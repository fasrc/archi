"""The CLI reference must document the port values ``archi create`` actually refuses.

Issue #311 changed a silent fallback into a refusal. Before it, ``port: 0``, ``port: ""`` and
``port: null`` were dropped during port extraction, preflight passed, ``create --force`` tore
down the running deployment, and the run then failed at render time -- an outage caused by a
typo. Now the configured value is preserved and validated, and the run is refused *before* the
teardown. That is operator-visible in the only way that matters: a `create` that used to appear
to work now stops with a message, and documenting it only in the OpenSpec change leaves the
operator reading the published reference with no explanation (Greptile review, PR #317).

The refusal table is a promise, and prose has no compiler. This drives the check from the table
itself: every ``key: value`` the section publishes is parsed **as YAML** and fed to the real
preflight, so a row can neither name a value the code accepts nor quote a message the code does
not emit. The reverse direction is covered too -- a curated set of YAML spellings a port key can
carry must each be refused, and the categories must be represented in the section.

Parsing the rows as YAML rather than mapping literals by hand is deliberate. An earlier version
of this file hard-coded ``{"0": 0, '""': "", "null": None}`` and called it the complete set; it
was not (it missed ``~``, and both boolean spellings), it never exercised the ``external_port``
path the section describes, and it never checked the ``70000`` example the section publishes --
so it stayed green while the documentation over-promised (Codex review, PR #317).
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.cli.managers.templates_manager import extract_port_config, validate_port_config
from src.cli.utils.service_builder import ServiceBuilder

CLI_REFERENCE = (
    Path(__file__).resolve().parents[2] / "docs" / "docs" / "cli_reference.md"
)

_SECTION_HEADING = "### Rejected port values"

# A row's first cell holds one or more `key: value` spellings; the second holds the message.
_ROW_ASSIGNMENT = re.compile(r"`((?:port|external_port):[^`]*)`")

# Every YAML spelling a port key can carry that is not a usable port. Unlike the hand-rolled
# map this replaces, each entry is parsed by PyYAML, so `on`/`yes`/`~` resolve exactly as they
# will in a real configuration file.
_REFUSABLE_SPELLINGS = (
    "0",
    "70000",
    "-1",
    '""',
    "null",
    "~",
    "true",
    "false",
    "on",
    "off",
    "yes",
    "no",
    "notaport",
)

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


def _documented_assignments():
    """Every `<key>: <value>` the section publishes, as (key, parsed value, raw spelling)."""
    out = []
    for raw in _ROW_ASSIGNMENT.findall(_section()):
        key, _, literal = raw.partition(":")
        key, literal = key.strip(), literal.strip()
        if not literal:
            continue
        loaded = yaml.safe_load(f"v: {literal}")["v"]
        out.append((key, loaded, raw))
    return out


def _verdict(key, value, *, host_mode):
    """Run the real preflight with one configured key/value; return the message or None."""
    plan = ServiceBuilder.build_compose_config(
        name="docs-contract",
        verbosity=0,
        base_dir=Path("/tmp/docs-contract"),
        enabled_services=["chatbot"],
        host_mode=host_mode,
    )
    service_cfg = {"port": 7861} if key == "external_port" else {}
    service_cfg[key] = value
    base = {"services": {"chat_app": service_cfg}}
    cm = SimpleNamespace(get_configs=lambda: [base])
    try:
        validate_port_config(plan, cm, extract_port_config(plan, cm))
    except ValueError as exc:
        return str(exc)
    return None


def test_the_section_publishes_a_refusal_table_at_all():
    assert (
        _documented_assignments()
    ), "the section names no `port:`/`external_port:` values, so it promises nothing checkable"


def test_every_value_the_section_publishes_is_really_refused():
    # Guards against over-promising. `external_port` is checked in container mode, which is
    # the mode the section describes for it.
    accepted = [
        raw
        for key, value, raw in _documented_assignments()
        if _verdict(key, value, host_mode=(key == "port")) is None
    ]
    assert (
        not accepted
    ), f"the section lists {accepted} as refused, but preflight accepts them"


def test_every_message_the_section_quotes_is_a_message_the_code_emits():
    # Guards the quoted strings. The table renders the service as <service> and the path as
    # services.<service>.<key>; compare on the stable prefix the code produces.
    section = _section()
    for key, value, raw in _documented_assignments():
        message = _verdict(key, value, host_mode=(key == "port"))
        assert message is not None, raw
        head = message.split(" for ")[0]
        assert (
            head in section
        ), f"`{raw}` really produces {message!r}, but the section never quotes {head!r}"


@pytest.mark.parametrize("spelling", _REFUSABLE_SPELLINGS)
@pytest.mark.parametrize("key", ["port", "external_port"])
def test_no_unusable_yaml_spelling_survives_preflight(key, spelling):
    # Guards against under-promising, and is the regression pin for the boolean hole: `on`,
    # `yes` and `true` all load as True, and int(True) is 1, so without an explicit bool
    # guard preflight accepted `port: on` as port 1 (Codex review, PR #317).
    value = yaml.safe_load(f"v: {spelling}")["v"]
    assert (
        _verdict(key, value, host_mode=(key == "port")) is not None
    ), f"preflight accepts `{key}: {spelling}` (parsed as {value!r}), which is not a port"


def test_the_section_warns_about_the_boolean_spellings():
    # on/off/yes/no are the trap: they look like flags and load as booleans. An operator who
    # wrote `port: on` must find that here.
    section = _section()
    for word in ("on", "yes", "true"):
        assert (
            f"`port: {word}`" in section or f"`{word}`" in section
        ), f"the section never mentions the {word!r} spelling"


def test_the_section_states_that_omitting_a_port_is_not_an_error():
    # The distinction #311 encodes: an omitted key falls back to the registry default; only a
    # value the operator wrote is validated. Without this the refusal list reads as "every
    # service now needs an explicit port".
    section = _section()
    assert _OMISSION_RULE.search(
        section
    ), "the section never says an omitted port key is left alone"
    assert _DEFAULT_RULE.search(
        section
    ), "the section never says an omitted port key keeps its default"
    assert (
        _verdict("port", 7861, host_mode=False) is None
    ), "code changed; update this test"


def test_the_section_says_the_refusal_precedes_the_teardown():
    # The ordering is the point of the fix (fasrc/archi#287, #311): a refusable config must not
    # cost the operator a running deployment first.
    section = _section().lower()
    assert "before" in section, "the section never states the refusal ordering"
    assert (
        "--force" in section or "teardown" in section or "tear down" in section
    ), "the section never says what the refusal happens before"


def test_the_section_names_the_external_port_path():
    # external_port supplies the host-side port in container mode, so it is refused the same
    # way and the message names it. The earlier version of this test never covered that.
    section = _section()
    assert "external_port" in section, "the section never mentions external_port"
    message = _verdict("external_port", 0, host_mode=False)
    assert (
        message is not None and "external_port" in message
    ), "container-mode external_port: 0 must be refused naming the external_port key"
