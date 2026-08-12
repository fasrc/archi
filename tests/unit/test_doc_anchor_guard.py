"""``docs/docs/api_reference.md`` cites ``src/interfaces/chat_app/app.py`` by line number,
in two inline spellings that mean different things:

* ``` [`app.py:NNNN`][tag] ``` — **canonical**. ``NNNN`` MUST equal ``[tag]``'s link
  definition (``[tag]: https://.../app.py#L<n>``); the two spellings of the same anchor
  cannot disagree.
* ``` [`:NNNN`][tag] ``` — **abbreviated**. ``NNNN`` MAY cite a different line than
  ``[tag]``'s definition, as long as it is still a line within the region ``[tag]`` points
  at — the document uses this spelling to name a specific line (an event literal, a
  condition) inside that region rather than the region's own anchor line.

Both spellings, and the link definitions themselves, are verified the same way: the line
they name in ``app.py`` must contain a checked-in expected substring. That is the guard this
file exists to run. A red result means the doc anchors drifted — update
``docs/docs/api_reference.md``, not this test.

Reads both files as source text with ``pathlib.Path``, following
``tests/unit/test_require_auth.py::TestNoUnreachableStatementRemains``; imports nothing from
``src``.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_PY = REPO_ROOT / "src" / "interfaces" / "chat_app" / "app.py"
API_REFERENCE = REPO_ROOT / "docs" / "docs" / "api_reference.md"

_LINK_DEFINITION_RE = re.compile(
    r"^\[([^\]]+)\]:\s*"
    r"https://github\.com/fasrc/archi/blob/dev/src/interfaces/chat_app/app\.py#L(\d+)\s*$",
    re.MULTILINE,
)

_INLINE_CITATION_RE = re.compile(
    r"\[`(?:app\.py:(\d+)(?:-(\d+))?|:(\d+))`\]\[([^\]]+)\]"
)


def _link_definitions(text):
    """Return ``{tag: line_no}`` for every ``[tag]: .../app.py#L<n>`` definition."""
    return {tag: int(line_no) for tag, line_no in _LINK_DEFINITION_RE.findall(text)}


def _inline_citations(text):
    """Return one record per inline citation into ``app.py``.

    Each record is ``(doc_line, spelling, start, end_or_None, tag)``, where ``spelling``
    is one of ``"canonical"`` (`` [`app.py:N`][tag] ``), ``"abbreviated"``
    (`` [`:N`][tag] ``), or ``"range"`` (`` [`app.py:N-M`][tag] ``). ``end_or_None`` is the
    second endpoint for a range citation and ``None`` otherwise.
    """
    records = []
    for doc_line, line in enumerate(text.splitlines(), start=1):
        for match in _INLINE_CITATION_RE.finditer(line):
            canonical_start, canonical_end, abbrev_start, tag = match.groups()
            if canonical_start is not None and canonical_end is not None:
                spelling, start, end = "range", int(canonical_start), int(canonical_end)
            elif canonical_start is not None:
                spelling, start, end = "canonical", int(canonical_start), None
            else:
                spelling, start, end = "abbreviated", int(abbrev_start), None
            records.append((doc_line, spelling, start, end, tag))
    return records
