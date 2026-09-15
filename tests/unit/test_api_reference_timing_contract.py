"""The API reference's omitted-value lists must match what the parser actually accepts.

``docs/docs/api_reference.md`` documents each optional timing field by enumerating the
values a client may send to mean *"I am not supplying this field"*. That enumeration is a
promise, and prose has no compiler: when this change put an ``isinstance(value, bool)``
check in front of the falsey guard, ``false`` stopped being an omission and became a
**400** -- but it was still listed among the omission-equivalent values in both rows, so a
client following the table would send ``false`` to omit a field and get the 400 instead
(Codex review, PR #203).

Rather than pin a fixed wording, this reads the enumeration out of the table and compares
it -- as a set, in both directions -- against the literals the real parser actually
normalizes to ``0``. Listing a value the code refuses fails, and so does dropping one the
code accepts.

The doc's *rejection* prose is only checked to the extent that the row must still mention
the boolean rule and name a 400 (automating a manual `grep` from this change's task list).
Pinning it any harder would mean guessing at sentence shape, and the behaviour itself is
already covered from the code end by ``test_chat_timing_field_validation.py``.
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

_BOOLEAN_REFUSAL = re.compile(r"boolean")

# A JSON literal as written in the table -> the Python value the JSON decoder produces.
# An unmapped literal fails the test rather than being skipped: a doc rewritten into a
# notation this table does not know would otherwise pass vacuously.
#
# This is deliberately the COMPLETE set of falsey JSON values, plus `true` as the control.
# JSON has exactly six falsey literals -- `null`, `false`, `0`, `""`, `[]`, `{}` (`0.0`,
# `-0` and `0e0` all decode to the same zero) -- which is what lets the parity check below
# treat "the parser accepts it" as the whole truth rather than a sample of it.
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


def _literals_the_parser_treats_as_absent(field):
    """Ground truth: which JSON literals ``field``'s parser normalizes to ``0``.

    Derived by execution rather than hardcoded, so the expected set cannot itself drift
    away from the code it is supposed to describe.
    """
    parse = FIELD_PARSERS[field]
    absent = set()
    for literal, value in _JSON_LITERALS.items():
        try:
            result = parse(value)
        except InvalidClientTiming:
            continue
        if result == 0:
            absent.add(literal)
    return absent


@pytest.mark.parametrize("field", sorted(FIELD_PARSERS))
class TestDocumentedOmissionValuesAreActuallyOmissions:
    def test_the_documented_list_matches_the_parser_exactly(self, field):
        """Parity in both directions, against a set derived from the parser itself.

        Equality rather than "everything listed works" is the point. A one-directional
        check passes when the table quietly drops `[]` or `{}`, which is drift in the
        opposite direction: a client is then told a value it may legitimately send is
        invalid. Because `_JSON_LITERALS` is the complete set of falsey JSON literals,
        set equality here means the row is exactly right, not merely not-wrong.
        """
        documented = _documented_omission_literals(field)
        expected = _literals_the_parser_treats_as_absent(field)

        assert set(documented) == expected, (
            f"the `{field}` omission list is {sorted(set(documented))} but the parser "
            f"treats exactly {sorted(expected)} as 'not supplied'.\n"
            f"  listed but refused: {sorted(set(documented) - expected)} -- a client "
            "following the table gets a 400 instead of the documented omission.\n"
            f"  accepted but unlisted: {sorted(expected - set(documented))} -- a client "
            "is told a usable value is invalid.\n"
            "Fix the doc or the code, not this test."
        )

    def test_no_duplicate_entries_in_the_documented_list(self, field):
        """Set equality above would hide a literal listed twice."""
        documented = _documented_omission_literals(field)

        assert len(documented) == len(
            set(documented)
        ), f"the `{field}` omission list repeats an entry: {documented}."

    def test_the_row_still_documents_that_a_boolean_is_refused(self, field):
        """Automates task 5.4's manual `grep -c boolean` on this change.

        Deliberately narrow: it proves the row still *mentions* the boolean rule and that
        a 400 is named on the row, not that the sentence around it is correct. The
        parity check above is what actually pins behaviour; this only stops the boolean
        guarantee from vanishing from the table without anyone noticing, which is how
        `false` came to be listed in two contradictory places to begin with.
        """
        row = _field_row(field)

        assert _BOOLEAN_REFUSAL.search(row), (
            f"the `{field}` row no longer mentions booleans at all. Both booleans are a "
            "400 (`request_validation.py:26`); a client reading this row would not know."
        )
        assert "**400**" in row, (
            f"the `{field}` row documents no 400 response, so its rejection rules went "
            "missing along with the boolean one."
        )
