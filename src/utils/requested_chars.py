"""The caller-supplied size rule shared by the agent tools and the uploader.

``resolve_requested_chars`` lives here, not in
``src.archi.pipelines.agents.tools.result_limits``, so the uploader's catalog
document endpoint (issue #260) can use it without importing
``src.archi.pipelines``, whose ``__init__`` loads every agent class and the MCP
adapters. ``result_limits`` re-exports both names.
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
