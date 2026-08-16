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

from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from langchain.agents.middleware.context_editing import ClearToolUsesEdit
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from src.archi.pipelines.agents.tools.result_limits import clamp_result
from src.archi.pipelines.agents.utils.context_budget import (
    ContextBudget,
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


def build_context_middleware(
    *,
    model: Any,
    context_window: Any,
    config: Optional[Dict[str, Any]] = None,
    pipeline_config: Optional[Dict[str, Any]] = None,
    tool_budgets: Optional[Dict[str, int]] = None,
    retrieval_tool_name: str = DEFAULT_RETRIEVAL_TOOL,
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

    Note that only a real YAML boolean disables the bound: ``enabled: 0`` is an
    invalid value, and an invalid value is logged and ignored rather than
    silently removing the protection the other settings configure.
    """
    settings = read_settings(config, pipeline_config)
    budget = resolve_budget(
        context_window=context_window,
        output_cap=resolve_output_cap(model, None),
        settings=settings,
        # The tool name lives in exactly one place: the caller hands over its
        # whole budget map and never has to know which entry matters, so raising
        # the retrieval tool's budget re-sizes the exemption without the call
        # site changing.
        retrieval_call_budget=(tool_budgets or {}).get(retrieval_tool_name, 0),
    )
    if budget is None:
        return []
    return [
        ContextBudgetMiddleware(budget=budget, retrieval_tool_name=retrieval_tool_name)
    ]
