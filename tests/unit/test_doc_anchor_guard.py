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


# {line_no: expected substring of app.py's line at line_no}, one entry per distinct line
# number anchored by a link definition or inline citation in api_reference.md (33 total).
# Derived from app.py's current content, EXCEPT [ovrwarn2]: the doc still cites L2138 (a
# comment tail) but the substring below is for L2143 (its warning yield) — the fix this
# change makes. That mismatch is what makes this guard fail red before the doc is repaired.
_EXPECTED_SUBSTRINGS = {
    1695: "if not history:",
    1767: 'if include_tool_steps and hasattr(message, "tool_calls") and message.tool_calls:',
    1773: '"type": "step"',
    2085: "yield {",
    2102: "if provider and model:",
    2112: '"status": 400, "message": str(e)}',
    2118: 'yield {"type": "warning", "message": f"Using default model: {e}"}',
    2121: "if (",
    2143: "yield {",
    2372: '"type": "tool_start"',
    2385: '"type": "tool_output"',
    2399: '"type": "tool_end"',
    2412: '"type": "thinking_start"',
    # [thinkgate] and [thinkgate2] both gate on this line's text (design.md Decision 4).
    2418: "if include_tool_steps:",
    2424: '"type": "thinking_end"',
    2432: "if include_tool_steps:",
    2435: 'elif event_type == "text":',
    2438: "if content and include_agent_steps:",
    2441: '"type": "chunk"',
    2450: '"type": "text"',
    2459: "else:",
    2472: "if include_agent_steps:",
    2483: '"type": "chunk"',
    2572: '"type": "usage"',
    2596: '"type": "final"',
    2611: '"model_used": reported_model',
    2645: "except Exception as exc:",
    2806: "self.require_auth(self.get_chat_response)",
    3290: 'request.form.get("username")',
    4770: 'timestamps["client_sent_msg_ts"]',
    4820: "if not client_id:",
    4837: "yield json.dumps(",
    4864: "stream_with_context(_event_stream())",
}
