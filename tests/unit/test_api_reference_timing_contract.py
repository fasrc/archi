"""The API reference's omitted-value lists must match what the parser actually accepts.

``docs/docs/api_reference.md`` documents each optional timing field by enumerating the
values a client may send to mean *"I am not supplying this field"*. That enumeration is a
promise, and prose has no compiler: when this change put an ``isinstance(value, bool)``
check in front of the falsey guard, ``false`` stopped being an omission and became a
**400** -- but it was still listed among the omission-equivalent values in both rows, so a
client following the table would send ``false`` to omit a field and get the 400 instead
(Codex review, PR #203).

Rather than pin a fixed wording, this reads the enumeration out of the table and
*executes* every literal in it against the real parser. Any value the doc calls an
omission must normalize to ``0``, so a value the code refuses cannot be listed there
without turning this test red.

Only the omission list is checked, not the doc's rejection list. The rejection side is
already pinned from the code end by ``test_chat_timing_field_validation.py``, and it is
written as prose rather than a delimited enumeration, so extracting it would mean guessing
at sentence shape -- a brittle test guarding an already-covered claim.
"""

import re
from pathlib import Path

import pytest

from src.interfaces.chat_app.request_validation import (
    InvalidClientTiming,
    parse_client_sent_msg_ts,
    parse_client_timeout,
)

API_REFERENCE = (
    Path(__file__).resolve().parents[2] / "docs" / "docs" / "api_reference.md"
)

# The enumeration is delimited by an em-dash in one row and by parentheses in the other.
# Both forms are matched so this test does not force the two cells into identical prose
# for its own convenience.
_OMISSION_LIST = re.compile(
    r"\*{0,2}falsey\*{0,2} values? [—(]\s*((?:`[^`]+`(?:, )?)+)"
)

_BACKTICKED = re.compile(r"`([^`]+)`")

# A JSON literal as written in the table -> the Python value the JSON decoder produces.
# An unmapped literal fails the test rather than being skipped: a doc rewritten into a
# notation this table does not know would otherwise pass vacuously.
_JSON_LITERALS = {
    "null": None,
    "0": 0,
    "false": False,
    "true": True,
    '""': "",
    "[]": [],
    "{}": {},
}

FIELD_PARSERS = {
    "client_sent_msg_ts": parse_client_sent_msg_ts,
    "client_timeout": parse_client_timeout,
}


def _field_row(field):
    """The API-reference table row documenting ``field``."""
    for line in API_REFERENCE.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| `{field}` |"):
            return line
    raise AssertionError(f"no `{field}` row found in {API_REFERENCE}")


def _documented_omission_literals(field):
    """The JSON literals the table lists as meaning "field omitted"."""
    match = _OMISSION_LIST.search(_field_row(field))
    assert match is not None, (
        f"the `{field}` row no longer enumerates the values that mean 'not supplied'. "
        "This test executes that list against the parser; without it the documented "
        "contract is unchecked. Restore the enumeration or delete this test knowingly."
    )
    return _BACKTICKED.findall(match.group(1))


@pytest.mark.parametrize("field", sorted(FIELD_PARSERS))
class TestDocumentedOmissionValuesAreActuallyOmissions:
    def test_the_enumeration_is_present_and_not_vacuous(self, field):
        """Guards the guard: an empty or truncated list would pass every check below."""
        literals = _documented_omission_literals(field)

        assert len(literals) >= 4, (
            f"the `{field}` omission list shrank to {literals}. The falsey guard accepts "
            "null, 0, \"\", [] and {} as 'not supplied'; a shorter list means the doc "
            "stopped describing the code."
        )

    def test_every_listed_value_normalizes_to_zero(self, field):
        parse = FIELD_PARSERS[field]

        for literal in _documented_omission_literals(field):
            assert literal in _JSON_LITERALS, (
                f"the `{field}` omission list contains `{literal}`, which this test "
                f"cannot map to a JSON value. Add it to _JSON_LITERALS so the claim is "
                "actually executed."
            )
            value = _JSON_LITERALS[literal]

            try:
                result = parse(value)
            except InvalidClientTiming as exc:
                raise AssertionError(
                    f"the API reference lists `{literal}` as a way to omit "
                    f"`{field}`, but the parser refuses it with a 400: {exc}. A client "
                    "following the table would get an error instead of the documented "
                    "omission behaviour -- fix the doc or the code, not this test."
                ) from exc

            assert result == 0, (
                f"`{literal}` is documented as omitting `{field}`, so it must normalize "
                f"to 0 (the absent-value sentinel), not {result!r}."
            )
