"""In-loop context budget arithmetic for the ReAct agent (issue #235).

Purpose
-------
Decide, before a request runs, **how many prompt tokens the agent may send** and
with what settings the in-loop trimming should operate. This module talks to no
model and edits no messages: it reads configuration, produces numbers, and the
middleware wrapper applies them on every model call.

It exists as its own module for two reasons. ``base_react.py`` is ~2200 lines and
``interfaces/chat_app/app.py`` is not imported by unit tests, so arithmetic placed
in either cannot reach the project's 80% patch-coverage floor. Keeping it here
makes every branch — invalid config, an undeterminable window, an oversized
exemption — directly testable, and leaves those files holding call sites only.
``config_fingerprint.py`` is the existing precedent for the pattern.

The budget
----------
::

    trigger = context_window - generation_reserve - counting_margin

**context_window** is the model's *total* sequence length, covering the prompt
**and** the generation. A budget equal to the window is therefore exceeded by any
answer the model produces, which is what the two subtractions are for.

**generation_reserve** leaves room for the answer. It is
``max(percentage_floor, effective_output_cap)``. The percentage alone is not
safe: Claude Sonnet 4 is configured ``context_window=200000,
max_output_tokens=64000``, so a 15% reserve would permit a 170K prompt while the
provider is simultaneously asked to allow 64K of generation — 234K against a 200K
window, rejected before the trigger is ever consulted.

**counting_margin** covers the gap between the approximate token counter and the
provider's real tokenizer. It is a **separate term and not a share of the
reserve**: once the reserve is fully allocated to the output cap, nothing is left
to absorb an undercount, and the usual "a later model call re-evaluates it"
argument does not apply — the provider rejects *that* call and the reactive
overflow handler ends the run, so there is no later call. This margin exists for
that gap alone; do not re-purpose it (the 15% has already been re-purposed twice).

Effective output cap
--------------------
The declared ``ModelInfo.max_output_tokens`` is **not** the cap that applies, in
either direction:

* ``AnthropicProvider.get_chat_model`` applies the declared value only when the
  caller supplied no ``max_tokens``; ``extra_kwargs`` may supply a larger one,
  which then wins at runtime while the budget was sized against the metadata.
* ``LocalProvider`` declares a ``max_output_tokens`` and passes it to neither
  ``ChatOllama`` nor ``ChatOpenAI``, so its declared value is inert unless an
  operator sets one.

``resolve_output_cap`` therefore prefers a cap configured on the bound model and
falls back to the declared value, and callers pass ``declared_cap=None`` for
providers that do not apply their own metadata.

Retrieval exemption
-------------------
Retrieval results carry the grounding evidence the answer cites, so they are
exempt from clearing — but only while that exemption is provably cheap. Its worst
case is sized from values in force at runtime::

    exempt_floor = retrieval_call_budget * per_result_tokens

Both terms are in **tokens**, the budget's own unit. Sizing this by multiplying a
character limit and comparing the product against a token budget reads as cheap
while costing several times its share, and exempt content cannot afterwards be
cleared. When the floor exceeds ``exemption_fraction`` of the budget the
exemption is **dropped with a warning** rather than honoured: the design fails
toward the bound holding, never toward a silent floor.

Selection is bounded by count *and ordered*: the earliest results up to the call
budget are exempt. The per-turn budget permits its allowance of successful calls
before it begins returning synthetic refusals under the same tool name, so the
earliest results are the evidence and the newest are the refusals. Selecting by
recency inverts this — it protects refusals and exposes evidence.

Configuration
-------------
``services.chat_app.context_editing``, overridable per pipeline via
``pipeline_config.context_editing``, following the same three-layer lookup as
``tool_budgets``. Absent configuration yields the protective defaults. An invalid
value is logged and replaced by its default; it never disables the bound.

Note that ``enabled: false`` disables **in-loop editing only** — the per-tool
source clamps in ``tools/result_limits.py`` are unconditional, so it is not a
full rollback of issue #235's changes.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set

from src.utils.logging import get_logger

logger = get_logger(__name__)

# Share of the window held back for the model's answer when no effective output
# cap applies. Matches the pre-loop prompt budget in ``base_react.py`` so a single
# convention governs both.
DEFAULT_GENERATION_RESERVE_FRACTION = 0.15

# Share held back to cover approximate-vs-real tokenizer drift. Separate from the
# reserve on purpose — see the module docstring.
DEFAULT_COUNTING_MARGIN_FRACTION = 0.05

# Most recent tool results preserved unreduced. Upstream's own default.
DEFAULT_KEEP = 3

# Per-result token ceiling used for the exemption/preserve arithmetic.
DEFAULT_PER_RESULT_TOKENS = 1500

# Largest share of the budget the retrieval exemption may occupy before it is
# dropped in favour of the bound.
DEFAULT_EXEMPTION_FRACTION = 1.0 / 3.0

_CONFIG_KEY = "context_editing"


@dataclass(frozen=True)
class ContextEditingSettings:
    """Validated knobs for the in-loop bound."""

    enabled: bool
    reserve_fraction: float
    margin_fraction: float
    keep: int
    per_result_tokens: int
    exemption_fraction: float


@dataclass(frozen=True)
class ContextBudget:
    """The resolved numbers a request runs with."""

    context_window: int
    generation_reserve: int
    counting_margin: int
    trigger: int
    keep: int
    per_result_tokens: int
    exempt_floor_tokens: int
    exempt_count: int


def _positive_int(value: Any) -> Optional[int]:
    """Return *value* as a positive int, or None if it is not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _coerce_fraction(value: Any, default: float, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid context_editing.%s=%r; using default %s", name, value, default
        )
        return default
    if not 0.0 < parsed < 1.0:
        logger.warning(
            "Out-of-range context_editing.%s=%r (expected 0 < x < 1); using default %s",
            name,
            value,
            default,
        )
        return default
    return parsed


def _coerce_positive_int(value: Any, default: int, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid context_editing.%s=%r; using default %s", name, value, default
        )
        return default
    if parsed <= 0:
        logger.warning(
            "Non-positive context_editing.%s=%r; using default %s", name, value, default
        )
        return default
    return parsed


def read_settings(
    config: Optional[Dict[str, Any]], pipeline_config: Optional[Dict[str, Any]]
) -> ContextEditingSettings:
    """Merge context-editing settings across the three established layers.

    Order (highest priority last): class defaults ->
    ``services.chat_app.context_editing`` -> ``pipeline_config.context_editing``.

    An invalid value is logged and replaced by its own default. It never disables
    the bound, because a typo in one knob should not silently remove the
    protection the other knobs configure.
    """
    merged: Dict[str, Any] = {}
    if isinstance(config, dict):
        services = config.get("services")
        if isinstance(services, dict):
            chat = services.get("chat_app")
            if isinstance(chat, dict):
                block = chat.get(_CONFIG_KEY)
                if isinstance(block, dict):
                    merged.update(block)
    if isinstance(pipeline_config, dict):
        block = pipeline_config.get(_CONFIG_KEY)
        if isinstance(block, dict):
            merged.update(block)

    enabled = merged.get("enabled", True)
    return ContextEditingSettings(
        enabled=bool(enabled) if isinstance(enabled, bool) else True,
        reserve_fraction=_coerce_fraction(
            merged.get("reserve_fraction", DEFAULT_GENERATION_RESERVE_FRACTION),
            DEFAULT_GENERATION_RESERVE_FRACTION,
            "reserve_fraction",
        ),
        margin_fraction=_coerce_fraction(
            merged.get("margin_fraction", DEFAULT_COUNTING_MARGIN_FRACTION),
            DEFAULT_COUNTING_MARGIN_FRACTION,
            "margin_fraction",
        ),
        keep=_coerce_positive_int(
            merged.get("keep", DEFAULT_KEEP), DEFAULT_KEEP, "keep"
        ),
        per_result_tokens=_coerce_positive_int(
            merged.get("per_result_tokens", DEFAULT_PER_RESULT_TOKENS),
            DEFAULT_PER_RESULT_TOKENS,
            "per_result_tokens",
        ),
        exemption_fraction=_coerce_fraction(
            merged.get("exemption_fraction", DEFAULT_EXEMPTION_FRACTION),
            DEFAULT_EXEMPTION_FRACTION,
            "exemption_fraction",
        ),
    )


def resolve_output_cap(model: Any, declared_cap: Optional[int]) -> Optional[int]:
    """Return the output cap that will actually apply to calls on *model*.

    A cap configured on the bound model takes precedence over the provider's
    declared metadata; the metadata is the fallback. Callers pass
    ``declared_cap=None`` for providers that never apply their own declared value.
    """
    configured = _positive_int(getattr(model, "max_tokens", None))
    if configured is not None:
        return configured
    return _positive_int(declared_cap)


def resolve_budget(
    *,
    context_window: Any,
    output_cap: Optional[int],
    settings: ContextEditingSettings,
    retrieval_call_budget: int = 0,
) -> Optional[ContextBudget]:
    """Resolve the in-loop budget, or None when no bound should be installed.

    Returns None — failing open to today's behaviour — when the context window
    cannot be determined or is not a positive integer, when the settings disable
    the bound, or when the reserve and margin together would consume the whole
    window (a non-positive budget would clear everything rather than protect
    anything).
    """
    if not settings.enabled:
        return None
    window = _positive_int(context_window)
    if window is None:
        logger.debug(
            "No in-loop context bound: unusable context window %r", context_window
        )
        return None

    percentage_reserve = int(window * settings.reserve_fraction)
    reserve = max(percentage_reserve, output_cap or 0)
    margin = int(window * settings.margin_fraction)
    trigger = window - reserve - margin
    if trigger <= 0:
        logger.warning(
            "No in-loop context bound: reserve (%d) + margin (%d) consume the "
            "%d-token window",
            reserve,
            margin,
            window,
        )
        return None

    exempt_floor = max(0, retrieval_call_budget) * settings.per_result_tokens
    exempt_count = max(0, retrieval_call_budget)
    if exempt_floor > trigger * settings.exemption_fraction:
        logger.warning(
            "Dropping the retrieval exemption: its worst case (%d calls x %d "
            "tokens = %d) exceeds %.0f%% of the %d-token budget. Retrieval "
            "results will be cleared like any other tool result.",
            retrieval_call_budget,
            settings.per_result_tokens,
            exempt_floor,
            settings.exemption_fraction * 100,
            trigger,
        )
        exempt_count = 0

    return ContextBudget(
        context_window=window,
        generation_reserve=reserve,
        counting_margin=margin,
        trigger=trigger,
        keep=settings.keep,
        per_result_tokens=settings.per_result_tokens,
        exempt_floor_tokens=exempt_floor,
        exempt_count=exempt_count,
    )


def select_exempt_indices(
    messages: Sequence[Any], tool_name: str, limit: int
) -> Set[int]:
    """Indices of the tool results exempt from clearing.

    Selects the **earliest** results bearing *tool_name*, up to *limit*. The
    per-turn call budget permits its allowance of successful calls before it
    starts returning synthetic refusals under the same name, so the earliest
    results are the evidence and everything after them is a refusal. Selecting by
    recency would exempt exactly the wrong messages — protecting refusals while
    handing the grounding evidence to the clearing pass.
    """
    if limit <= 0:
        return set()
    chosen: List[int] = []
    for idx, message in enumerate(messages):
        if len(chosen) >= limit:
            break
        if getattr(message, "type", None) != "tool":
            continue
        if getattr(message, "name", None) != tool_name:
            continue
        chosen.append(idx)
    return set(chosen)
