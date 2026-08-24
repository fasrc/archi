"""In-loop context middleware for the ReAct agent (issue #235).

Purpose
-------
Keep the **complete request** — system prompt, tool schemas and messages
together — under the budget derived by ``context_budget.py``, on every model
call *inside* the agent loop. The pre-loop budget in ``_prepare_agent_inputs``
runs once, before the loop, over conversation history only; nothing bounds what
the loop itself accumulates, which is the mechanism behind #235.

Why this wrapper is ours
------------------------
Only ``ClearToolUsesEdit`` is reused from langchain. ``ContextEditingMiddleware``
is deliberately **not** subclassed: on the pinned 1.0.3 its token counter is a
closure defined inside each of ``wrap_model_call`` and ``awrap_model_call``, so
"overriding the counter" means copying both wrapper bodies. Owning the wrapper
is the smaller and more honest dependency, and it is what makes the four
behaviours below expressible at all.

What the wrapper adds over upstream
-----------------------------------
**1. Complete-request counting.** Upstream's ``"approximate"`` mode counts
``request.messages`` and nothing else. The system prompt and the tool schemas
are sent on every call and charged on every call — here, 70 tokens for a single
tool schema against 5 for a short message list. A wrapper that ignores them
declares a request within budget that the provider then rejects.

**2. A universal per-result ceiling, applied first.** ``ClearToolUsesEdit``
selects candidates by recency across *all* tool results, so ``keep`` preserves
whatever ran most recently regardless of which tool produced it. A ceiling
enforced per-tool at the source therefore lapses the moment any other tool is
enabled — an MCP tool, a caller-supplied one — and the preserved results are
unbounded again. The ceiling here applies to every tool result in the request,
whatever its name.

Ordering is load-bearing: clamping runs **before** the clearing decision,
because clamping one oversized result can bring the request back under the
trigger on its own. Deciding to clear first discards history that never needed
to go.

**3. Exemption by count, not by name.** Retrieval results carry the grounding
evidence the answer cites. Upstream's ``exclude_tools`` exempts *every* message
bearing the name, which cannot express the count-bounded exemption the budget
sizes its worst case from, so it is left empty and the selection happens here.
Exemption from *clearing* is not exemption from the *ceiling*.

**4. State isolation.** ``apply`` mutates the list it is handed. Handing it
``request.messages`` writes placeholders into conversation state, so every later
turn inherits them — a permanent loss, not a transient view. The wrapper reduces
a shallow copy and hands the model a new request via ``override``; ``apply`` only
rebinds list slots to ``model_copy`` results and never mutates a ``ToolMessage``
in place, so a shallow copy is sufficient.

After reduction the wrapper **re-measures**. When the request is still over
budget — non-reducible content alone can exceed it — it logs the measured
overage rather than reporting success, and the pre-existing reactive overflow
handler remains the last-resort net.
"""

from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from langchain.agents.middleware.context_editing import ClearToolUsesEdit
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from src.archi.pipelines.agents.tools.result_limits import clamp_result
from src.archi.pipelines.agents.utils.context_budget import (
    ContextBudget,
    positive_int,
    read_settings,
    resolve_budget,
    resolve_output_cap,
    select_exempt_indices,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

# ``count_tokens_approximately``'s own default, used to convert the per-result
# token ceiling into the character ceiling ``clamp_result`` takes. Verified on
# the pinned version: a 4000-character tool result measures 1005 tokens.
CHARS_PER_TOKEN = 4

# The tool whose results carry the grounding evidence, exempt from clearing
# while the exemption is provably cheap (sized in ``context_budget.py``).
DEFAULT_RETRIEVAL_TOOL = "search_vectorstore_hybrid"

# Substituted for a cleared result. It says *why* the content is gone and tells
# the model not to spend another call re-fetching it — a bare marker invites
# exactly the retry loop the budget exists to prevent. The wording tracks
# ``spec.md:318`` ("cleared to stay within the context window", "do not
# re-request") and the tests assert those words, so reword the spec first.
#
# Kept terse on purpose: every cleared result pays for this text inside the very
# budget being defended (31 tokens per cleared result, against 22 for upstream's
# bare marker), so prose here is charged back against the space it reclaims.
DEFAULT_PLACEHOLDER = (
    "[Tool result cleared to stay within the context window; do not re-request it.]"
)


def count_request_tokens(
    request: ModelRequest, messages: Optional[Sequence[BaseMessage]] = None
) -> int:
    """Approximate the token cost of the **complete** request.

    Counts the system prompt and the tool schemas alongside the messages,
    because all three are sent and charged on every call. *messages* overrides
    ``request.messages`` so a candidate reduction can be measured without
    building a request for it.

    Uses the approximate counter only — never ``get_num_tokens_from_messages``,
    which would put a provider round trip or a tiktoken dependency in the hot
    path of every model call, and which some bound models do not implement.
    """
    candidate = list(request.messages if messages is None else messages)
    prefix: List[BaseMessage] = (
        [SystemMessage(content=request.system_prompt)] if request.system_prompt else []
    )
    return count_tokens_approximately(prefix + candidate, tools=request.tools or None)


def _clamp_content(content: Any, ceiling_chars: int) -> Optional[Any]:
    """Clamp a tool result's content, whatever shape it arrives in.

    Returns ``None`` when there is nothing clampable, so the caller can leave
    the message alone rather than corrupt it.

    Content is not always a string. ``langchain_mcp_adapters`` returns a
    **list** whenever a tool yields two or more text blocks, and those tools are
    merged into the agent's toolset — so the shape most likely to carry an
    unbounded payload is the one a naive string clamp raises ``TypeError`` on.
    Blocks arrive both as plain strings and as ``{"type": "text", ...}`` dicts;
    the text inside each is truncated and the structure is left intact, because
    ``model_copy`` performs no validation and a malformed replacement would fail
    later, inside the provider adapter, with no trace of its origin.
    """
    if isinstance(content, str):
        return clamp_result(content, ceiling_chars)
    if not isinstance(content, list):
        return None

    slots = [
        idx
        for idx, block in enumerate(content)
        if isinstance(block, str)
        or (isinstance(block, dict) and isinstance(block.get("text"), str))
    ]
    if not slots:
        return None

    share = max(1, ceiling_chars // len(slots))
    blocks = list(content)
    for idx in slots:
        block = blocks[idx]
        if isinstance(block, str):
            blocks[idx] = clamp_result(block, share)
        else:
            blocks[idx] = {**block, "text": clamp_result(block["text"], share)}
    return blocks


def clamp_tool_results(
    messages: Sequence[BaseMessage], per_result_tokens: int
) -> List[BaseMessage]:
    """Return a new list with every oversized tool result truncated.

    Applies to **every** tool result regardless of which tool produced it: the
    preserve count selects by recency across all tools, so a per-tool ceiling
    does not bound what survives.
    """
    ceiling_chars = max(1, per_result_tokens * CHARS_PER_TOKEN)
    clamped: List[BaseMessage] = list(messages)
    for idx, message in enumerate(clamped):
        if not isinstance(message, ToolMessage):
            continue
        if count_tokens_approximately([message]) <= per_result_tokens:
            continue
        truncated = _clamp_content(message.content, ceiling_chars)
        if truncated is not None and truncated != message.content:
            clamped[idx] = message.model_copy(update={"content": truncated})
    return clamped


class ContextBudgetMiddleware(AgentMiddleware):
    """Bound the complete request on every model call inside the loop."""

    def __init__(
        self,
        *,
        budget: ContextBudget,
        retrieval_tool_name: str = DEFAULT_RETRIEVAL_TOOL,
        placeholder: str = DEFAULT_PLACEHOLDER,
    ) -> None:
        super().__init__()
        self.budget = budget
        self.retrieval_tool_name = retrieval_tool_name
        # Middleware may contribute tools; this one contributes none. Set
        # explicitly rather than relying on the base class attribute existing.
        self.tools = []
        self.edit = ClearToolUsesEdit(
            trigger=budget.trigger,
            # Load-bearing, not a default. Above zero, ``apply`` re-measures
            # after each clear and breaks early once it believes it has
            # reclaimed enough — counting tokens from results this wrapper is
            # about to restore, so the pass stops short and the restore hands
            # them straight back. The restore-after design is only correct at 0.
            clear_at_least=0,
            keep=budget.keep,
            # False avoids an upstream defect as well as being right: the
            # write-back at ``context_editing.py:141`` locates the AI message by
            # equality, so two value-equal AI messages collide and the wrong
            # slot's arguments are wiped. The model also needs the record of its
            # own call to make sense of a cleared result.
            clear_tool_inputs=False,
            # Deliberately empty: upstream's option exempts every message
            # bearing the name, which cannot express a count-bounded exemption.
            # The selection happens in ``_reduce`` instead.
            exclude_tools=(),
            placeholder=placeholder,
        )

    def _reduce(self, request: ModelRequest) -> ModelRequest:
        """Return a request whose messages fit the budget, leaving state intact.

        Both the sync and async wrappers delegate here so the two paths cannot
        drift apart. Never raises: LangGraph composes model handlers with no
        ``try``, so anything escaping here ends the user's turn — a bad trade
        for a middleware whose entire purpose is preventing a failure.
        """
        try:
            return self._reduce_unguarded(request)
        except Exception:
            logger.error(
                "In-loop context bound failed; sending the request unreduced. "
                "The reactive overflow handler remains the last-resort net.",
                exc_info=True,
            )
            return request

    def _reduce_unguarded(self, request: ModelRequest) -> ModelRequest:
        if not request.messages:
            return request

        def counter(messages: Sequence[BaseMessage]) -> int:
            return count_request_tokens(request, messages)

        trigger = self.budget.trigger
        if counter(request.messages) <= trigger:
            # No pressure, no edit. The ceiling is a response to crossing the
            # budget, not a permanent tax on every result the agent reads.
            return request

        before = counter(request.messages)

        # A new list, always — this copy is what keeps reduction out of state.
        working = clamp_tool_results(request.messages, self.budget.per_result_tokens)

        exempt: Dict[int, BaseMessage] = {
            idx: working[idx]
            for idx in select_exempt_indices(
                working, self.retrieval_tool_name, self.budget.exempt_count
            )
        }

        self.edit.apply(working, count_tokens=counter)

        # ``apply`` only rebinds slots — it never inserts or removes elements —
        # so restoring by index is safe and puts back the clamped evidence
        # rather than the original oversized content.
        cleared = list(working)
        for idx, message in exempt.items():
            working[idx] = message

        measured = counter(working)
        shed = 0
        if measured > trigger:
            # The exemption is best-effort, not absolute. With the shipped call
            # budget of 2 and ``keep`` of 3, a five-result turn makes the exempt
            # set *identical* to the clearable candidate set, so restoring all
            # of it undoes the entire pass and the request goes out over budget
            # having reclaimed nothing. Give entries back one at a time until
            # the bound holds — newest first, because the same ordering that
            # makes the earliest results the evidence makes the later ones the
            # likeliest refusals, and so the cheapest to lose.
            for idx in sorted(exempt, reverse=True):
                if measured <= trigger:
                    break
                if cleared[idx] is working[idx]:
                    continue  # apply left this one alone; nothing to give back
                working[idx] = cleared[idx]
                shed += 1
                measured = counter(working)

        if measured <= trigger:
            # The success path. Silent, this feature is indistinguishable from
            # disabled: an operator asking "is the bound doing anything" has only
            # the failure warnings to go on, and their absence means either "it
            # worked" or "it never ran". Three goldenset runs completed with the
            # bound installed and could not answer that question.
            logger.debug(
                "In-loop context bound reduced the request from %d to %d tokens "
                "(%d reclaimed against a %d-token budget; %d exempt retrieval "
                "results shed, %d preserved as most-recent)",
                before,
                measured,
                before - measured,
                trigger,
                shed,
                self.budget.keep,
            )

        if measured > trigger:
            logger.warning(
                "In-loop context bound: request still measures %d tokens after "
                "reduction, %d over the %d-token budget (%d exempt retrieval "
                "results shed, %d preserved as most-recent). Non-reducible "
                "content exceeds the budget on its own; the reactive overflow "
                "handler remains the last-resort net.",
                measured,
                measured - trigger,
                trigger,
                shed,
                self.budget.keep,
            )

        return request.override(messages=working)

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Any]
    ) -> Any:
        """Reduce the request, then invoke the model with the reduced view."""
        return handler(self._reduce(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        """Async twin of :meth:`wrap_model_call`, sharing ``_reduce``."""
        return await handler(self._reduce(request))


def _declared_for_model(
    windows: Mapping[str, int],
    provider_id: Optional[str],
    model_id: Optional[str],
) -> Optional[int]:
    """The declared window for one run's model, most specific first.

    A model id alone is not a full identity: two providers can each serve
    ``llama3.2``, at different real windows, and both can be unable to report
    one. Handing one provider's declared window to the other is the same
    borrowed-number defect ``declared_window_applies`` exists to prevent, so a
    ``provider/model`` key — when the operator wrote one — outranks the bare id.

    Falling back to the bare id keeps the simple, documented shape working for
    the ordinary case where a model id is served by exactly one provider.
    """
    if not model_id:
        return None
    if provider_id:
        qualified = windows.get(f"{provider_id}/{model_id}")
        if qualified is not None:
            return qualified
    return windows.get(model_id)


def build_context_middleware(
    *,
    model: Any,
    context_window: Any,
    config: Optional[Dict[str, Any]] = None,
    pipeline_config: Optional[Dict[str, Any]] = None,
    tool_budgets: Optional[Dict[str, int]] = None,
    retrieval_tool_name: str = DEFAULT_RETRIEVAL_TOOL,
    model_label: Optional[str] = None,
    model_id: Optional[str] = None,
    provider_id: Optional[str] = None,
    declared_window_applies: bool = True,
) -> List[AgentMiddleware]:
    """Build the in-loop middleware list for an agent, or an empty list.

    This is the whole construction path: config in, middleware out. It lives
    here rather than in ``base_react.py`` so every branch is unit-testable, and
    here rather than in ``context_budget.py`` because that module cannot import
    the middleware class without a cycle.

    **Always returns a list**, never ``None``. An empty list is the fail-open
    outcome — an undeterminable context window, an operator disabling the bound,
    or a reserve that consumes the window — and returning it keeps the call site
    a single expression with no logic of its own.

    ``model`` and ``context_window`` MUST describe the **same** model. They are
    sourced separately at the call site, and crossing them silently produces a
    budget neither model justifies: an override's 64000 output cap against the
    source model's 128000 window yields a 57600-token trigger for a model whose
    real window is 200000.

    ``declared_cap`` is deliberately not plumbed through to
    ``resolve_output_cap``. Every provider in this repository that actually
    enforces an output cap binds it onto the chat model as ``max_tokens``, which
    the model argument already carries; passing declared metadata as well would
    only inflate the reserve for providers that never apply it. The signature is
    keyword-only, so a provider that breaks that assumption can be accommodated
    without changing any caller.

    ``placeholder`` is likewise not a parameter. Its wording is a design
    decision tracked against the spec, and exposing it would let a call site
    silently reinstate an uninformative marker.

    An operator-declared ``context_window`` in the config takes precedence over
    the *context_window* argument. That is not a convenience: the derived window
    comes from matching the configured model name against a list compiled into
    the provider, and self-hosted models are absent from every such list by
    construction, so for many deployments the declared value is the only one
    there is.

    ``declared_window_applies=False`` withdraws that precedence, and a
    request-local view bound to an overriding model passes it. The declared
    value describes the model **this deployment serves**; letting it follow a
    view onto a different model is the same defect as inheriting the source's
    window, arriving by another route. Measured: a declared 32768 paired with an
    override's 64000 output cap makes the reserve exceed the window and disables
    the bound outright. The override's own window is used instead, and when that
    cannot be resolved nothing is installed — borrowing a number that describes
    a different model is what this parameter exists to prevent.

    ``context_windows`` (issue #262) is how an operator supplies that missing
    number. It maps a model id to a window, so an entry names the model it
    describes and cannot be applied to any other. That is why
    ``declared_window_applies=False`` withdraws only the **single** window and
    never a map hit: the flag exists to demand testimony about the override's
    own model, and a map entry keyed by the override's id is exactly that
    testimony. Suppressing it would reintroduce the fail-open the flag was
    added to expose.

    A map entry also outranks the single ``context_window`` for its own model
    when the run is not request-local at all. Both are operator testimony and
    differ only in specificity; the more specific statement is the one they
    meant. The alternative would leave an operator unable to correct the window
    for one model without deleting it for all of them.

    ``model_id`` is the run's effective model and ``provider_id`` the provider
    serving it, both passed explicitly. They are **not** recovered from
    ``model_label``: a model id may itself contain ``/`` (the measured case is
    ``palmfuture/Qwen3.8-27B-GPTQ-Int4``), so splitting the label yields the
    wrong id on precisely the deployments this map exists for.

    A key may be written as ``provider/model`` to scope it to one provider, and
    such a key outranks the bare id — see ``_declared_for_model``. Chat requests
    carry provider and model independently, so two providers can each serve one
    model id at different real windows; the bare form stays correct for the
    ordinary case where a model id has exactly one provider.

    ``model_label`` names the provider and model in the log line emitted when no
    window can be found. Without it that outcome is invisible, and a deployment
    protecting nothing looks exactly like a healthy one.

    Note that only a real YAML boolean disables the bound: ``enabled: 0`` is an
    invalid value, and an invalid value is logged and ignored rather than
    silently removing the protection the other settings configure.
    """
    settings = read_settings(config, pipeline_config)
    declared = _declared_for_model(settings.context_windows, provider_id, model_id)
    if declared is None and declared_window_applies:
        declared = settings.context_window
    window = context_window if declared is None else declared
    budget = resolve_budget(
        context_window=window,
        output_cap=resolve_output_cap(model, None),
        settings=settings,
        # The tool name lives in exactly one place: the caller hands over its
        # whole budget map and never has to know which entry matters, so raising
        # the retrieval tool's budget re-sizes the exemption without the call
        # site changing.
        retrieval_call_budget=(tool_budgets or {}).get(retrieval_tool_name, 0),
    )
    if budget is None:
        # Distinguish the two ways of installing nothing. An operator who turned
        # it off does not need telling on every agent build, and a warning that
        # fires when nothing is wrong teaches people to ignore it. An absent
        # window is the opposite: nobody asked for it, and it is the failure
        # that hid this whole change being inert until the resolver was run
        # against a real deployment config.
        if settings.enabled and positive_int(window) is None:
            # Name a remedy the reader can actually use. On a request-local
            # override the single ``context_window`` is withdrawn by design, so
            # naming it here would send the operator to the one setting that
            # cannot work — in precisely the case this warning fires for.
            remedy = "services.chat_app.context_editing.context_window"
            if model_id and not declared_window_applies:
                remedy = (
                    "services.chat_app.context_editing.context_windows"
                    f"['{model_id}']"
                )
            logger.warning(
                "No in-loop context limit installed for %s: the provider reports "
                "no context window for this model, and none is configured. The "
                "window is matched by exact model name against a list compiled "
                "into the provider, so self-hosted and newly-released models "
                "will not be found. Set %s to install it.",
                model_label or "the configured model",
                remedy,
            )
        return []
    return [
        ContextBudgetMiddleware(budget=budget, retrieval_tool_name=retrieval_tool_name)
    ]
