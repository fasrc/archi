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

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_PY = REPO_ROOT / "src" / "interfaces" / "chat_app" / "app.py"
API_REFERENCE = REPO_ROOT / "docs" / "docs" / "api_reference.md"
