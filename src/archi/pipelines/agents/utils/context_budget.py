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
#
# **Measured, not guessed.** ``count_tokens_approximately`` assumes 4 characters
# per token. Against plain prose that is safe — markdown, agent specs and Python
# measure 3.7-5.0 chars/token, so the counter over-counts them and errs toward
# the bound holding. It is *not* safe for what fills the agent's loop: every
# retrieval snippet header carries a URL, a 32-hex resource hash, a path and a
# float score, and those tokenize densely.
#
# Measured over 557 real 800-character chunks of this repository's own
# documentation, each behind a retrieval header, as real-tokens ÷ counted-tokens:
#
#     p50  1.14x     p90  1.26x     p95  1.29x     p99  1.35x     max  1.72x
#
# At the former 5% this was not a margin at all: a 32768-token window resolved a
# 26215 trigger, and a prompt filled to it with corpus-average content really
# cost 31046 tokens — 3193 past the window once the reserve is added, so the
# provider rejected the very request the budget declared safe.
#
# 25% covers drift up to 1.42x. That sits above the 99th percentile of individual
# chunks, and well above what a *prompt* can reach: a filled prompt is a mixture
# of a dozen or more chunks, so its density concentrates near the p50-p75 mean
# rather than at any one chunk's maximum. A single pathological chunk cannot
# carry the whole prompt past the bound.
#
# What this does NOT cover, by choice: text with no prose at all — a passage that
# is purely command lines and paths measures 1.80x, and covering that would take
# a 38% margin, spending half the window to insure against a case a real
# 800-character documentation chunk does not reach. Those rely on the reactive
# overflow handler, which is what it is for.
#
# The real fix is a tokenizer rather than a character ratio; see issue #263.
# ``test_a_prompt_filled_to_the_trigger_still_fits_the_real_window`` pins this.
DEFAULT_COUNTING_MARGIN_FRACTION = 0.25

# Most recent tool results preserved unreduced. Upstream's own default.
DEFAULT_KEEP = 3

# Per-result token ceiling, used both for the floor arithmetic here and as the
# middleware's universal ceiling on retained results.
#
# This is a **backstop for tools that do not clamp themselves** (MCP tools,
# caller-supplied ones), not an override of the ones that do. It must therefore
# sit *above* the tuned source clamps in ``tools/``: the retriever's is 8000
# characters, which measures ~2011 tokens once the counter's per-message
# overhead is added. A ceiling below that silently re-truncates every full-size
# retrieval result — 25% of it — on every model call.
# ``test_ceiling_sits_above_both_source_clamps`` pins the relationship so
# raising either source clamp fails loudly here.
DEFAULT_PER_RESULT_TOKENS = 2100

# Below this, the character ceiling is too small to hold the truncation marker
# and ``clamp_result`` returns silently-unmarked partial text.
MIN_PER_RESULT_TOKENS = 16

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
    # An operator-declared context window, overriding whatever the provider
    # reports. ``None`` means "use the provider's". Defaulted so the other
    # construction sites keep working unchanged.
    context_window: Optional[int] = None


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


def positive_int(value: Any) -> Optional[int]:
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
        per_result_tokens=max(
            MIN_PER_RESULT_TOKENS,
            _coerce_positive_int(
                merged.get("per_result_tokens", DEFAULT_PER_RESULT_TOKENS),
                DEFAULT_PER_RESULT_TOKENS,
                "per_result_tokens",
            ),
        ),
        exemption_fraction=_coerce_fraction(
            merged.get("exemption_fraction", DEFAULT_EXEMPTION_FRACTION),
            DEFAULT_EXEMPTION_FRACTION,
            "exemption_fraction",
        ),
        context_window=_read_declared_window(merged.get("context_window")),
    )


def _read_declared_window(value: Any) -> Optional[int]:
    """Validate an operator-declared context window, or return None.

    Unlike the other settings this has no default to fall back to: None means
    "use whatever the provider reports", which is the behaviour without the
    setting at all. A bad value is therefore ignored rather than replaced, so a
    typo costs the operator the override and nothing else.

    ``positive_int`` rejects ``True`` along with the other non-integers. That
    matters here more than elsewhere: ``True`` is an ``int`` in Python, and a
    one-token window would clear every message on every call.
    """
    if value is None:
        return None
    window = positive_int(value)
    if window is None:
        logger.warning(
            "Invalid context_editing.context_window=%r; using the window the "
            "provider reports for the configured model",
            value,
        )
    return window


def resolve_model_window(provider: Any, model: Any) -> Optional[int]:
    """The context window *this already-built provider* reports for *model*.

    Called where a provider has been constructed from the deployment's own YAML
    — the only place a self-hosted or custom model ID has metadata at all. The
    by-name lookup an agent performs later builds its provider with no config
    and consults a ``ModelInfo`` list compiled into the package, where such an
    ID never appears; resolving here and carrying the number is what keeps a
    request-local override from silently having no bound.

    Never raises: a provider that cannot answer leaves the caller exactly where
    it would have been without this, rather than failing the request.
    """
    try:
        info = provider.get_model_info(model)
    except Exception:
        logger.debug(
            "Provider reported no metadata for %r; the window will be "
            "re-resolved by name if possible",
            model,
            exc_info=True,
        )
        return None
    return positive_int(getattr(info, "context_window", None))


def resolve_configured_model_window(
    provider: Any, model: Any, declared_models: Optional[Sequence[Any]]
) -> Optional[int]:
    """``resolve_model_window``, minus the windows nobody actually declared.

    A provider built from a deployment's YAML turns each entry of its ``models``
    list into ``ModelInfo(id=m, name=m, display_name=m)``, and
    ``ModelInfo.context_window`` **defaults to 128000**. Nothing about the
    deployment produced that number, but ``get_model_info`` returns it exactly
    as it would a measured one.

    Trusting it is worse than having no answer. Measured against this
    repository's own dev config, the configured self-hosted model reports 128000
    from a server launched with ``--max-model-len 32768`` — a budget four times
    the real window, which would overflow every request it was installed to
    protect. ``None`` routes to the documented fail-open instead, and issue #262
    tracks letting an operator declare these per model.

    Entries the config does **not** name are unaffected: those come from the
    provider's own compiled ``ModelInfo`` list, which is where a hosted model's
    genuine window lives.
    """
    for entry in declared_models or ():
        if getattr(entry, "id", None) == model:
            logger.debug(
                "Ignoring the window reported for %r: it is named in the "
                "deployment config, so the value is ModelInfo's default rather "
                "than a property of the server",
                model,
            )
            return None
    return resolve_model_window(provider, model)


def resolve_output_cap(model: Any, declared_cap: Optional[int]) -> Optional[int]:
    """Return the output cap that will actually apply to calls on *model*.

    A cap configured on the bound model takes precedence over the provider's
    declared metadata; the metadata is the fallback. Callers pass
    ``declared_cap=None`` for providers that never apply their own declared value.
    """
    configured = positive_int(getattr(model, "max_tokens", None))
    if configured is not None:
        return configured
    return positive_int(declared_cap)


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
    window = positive_int(context_window)
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

    # The exemption is not the only thing the clearing pass cannot touch: the
    # `keep` most recent results are preserved by upstream regardless of tool.
    # Sizing the guard against the exemption alone undercounts the irreducible
    # floor by `keep x per_result_tokens` — 6300 tokens at the defaults — and
    # the two sets are disjoint in the ordinary case (the exempt results are the
    # earliest, `keep` holds the latest), so the sum is the right bound. Where
    # they do overlap it over-estimates, which errs toward dropping the
    # exemption: the direction this design fails in on purpose.
    irreducible_floor = exempt_floor + settings.keep * settings.per_result_tokens
    if irreducible_floor > trigger * settings.exemption_fraction:
        logger.warning(
            "Dropping the retrieval exemption: the content it and the %d "
            "preserved results hold unconditionally (%d tokens) exceeds %.0f%% "
            "of the %d-token budget. Retrieval results will be cleared like any "
            "other tool result.",
            settings.keep,
            irreducible_floor,
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
