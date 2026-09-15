"""Enforced size ceilings for tool results (issue #235).

The in-loop context budget preserves the most recent tool results and exempts
the grounding retrieval evidence from clearing. Both are statements about
*retained* results, so neither bounds anything unless a retained result has an
enforced size of its own.

Two properties matter and are easy to get wrong:

* The ceiling applies to the **complete serialized return value**, not to the
  size a tool requests from its backend. ``fetch_catalog_document`` appends a
  path and a metadata preview after the server-limited text; the retriever
  interpolates uncapped ``title``/``url``/``resource_hash`` into every snippet
  header. Bounding the request leaves both over the limit.
* A caller-supplied size of ``0`` must mean "use the ceiling", never "no
  limit". ``0`` is falsy, and the catalog endpoint truncates only when its
  ``max_chars`` is truthy, so a zero forwarded downstream returns the whole
  document — the opposite of what the parameter name implies.
"""

from typing import Any

TRUNCATION_MARKER = "\n... (truncated to fit the agent's context budget)"


def resolve_requested_chars(requested: Any, ceiling: int) -> int:
    """Resolve a caller-supplied size against an enforced *ceiling*.

    A value larger than the ceiling is reduced to it. A non-positive or
    non-integer value is treated as a request for the ceiling rather than as
    "no limit", so a falsy or malformed input can never widen the bound.

    Booleans are rejected before coercion. ``True`` is an ``int`` in Python and
    ``int(True)`` is ``1``. ``positive_int`` in ``context_budget.py`` rejects
    booleans for the same reason.

    A value too small to hold the truncation marker is also treated as malformed.
    Such a request cannot produce a usable result — the tool returns a character
    or two of the document and the agent answers with no evidence, the opposite
    failure from the one this clamp exists to prevent. It is also the shape a
    coercion artifact takes: the tool's ``max_chars`` is annotated ``int``, so the
    ``@tool`` decorator's validation turns a model-supplied ``true`` into ``1``
    *before* the boolean check above can see it. Below the marker length
    ``clamp_result`` would return silently-unmarked partial text anyway, which is
    the same reason ``MIN_PER_RESULT_TOKENS`` exists.
    """
    if isinstance(requested, bool):
        return ceiling
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return ceiling
    if value < len(TRUNCATION_MARKER):
        return ceiling
    return min(value, ceiling)


def clamp_result(text: str, max_chars: int) -> str:
    """Bound *text* to *max_chars*, marking it when content was dropped.

    The marker counts against the budget, so the returned string never exceeds
    ``max_chars`` even when the marker itself is long relative to the limit.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    room = max_chars - len(TRUNCATION_MARKER)
    if room <= 0:
        # No space for both content and marker; the bound wins.
        return text[:max_chars]
    return text[:room] + TRUNCATION_MARKER
