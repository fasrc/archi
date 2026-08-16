"""
Unit tests for the in-loop context middleware (issue #235).

This is the layer that runs on **every** model call inside the ReAct loop and
keeps the request under the budget derived in ``context_budget.py``. The
arithmetic is tested there; what is tested here is the wrapper's behaviour, and
each of these tests exists because the obvious implementation gets it wrong:

* Upstream's ``ContextEditingMiddleware`` counts ``request.messages`` only. The
  system prompt and the tool schemas are part of the same request and are sent
  on every call, so a wrapper that ignores them declares a request within budget
  that the provider rejects. The counter here measures the **complete request**.
* ``ClearToolUsesEdit`` selects candidates by recency across *all* tool results,
  so ``keep`` preserves whatever ran most recently regardless of which tool
  produced it. A per-tool size ceiling therefore lapses the moment any other
  tool is enabled — an MCP tool, a caller-supplied one — and the preserved
  results are unbounded again. The ceiling here is **universal**.
* ``apply`` mutates the list it is handed. Handing it ``request.messages``
  directly writes placeholders into conversation state, so the *next* turn is
  served cleared content permanently. The wrapper reduces a copy.
* Ordering matters: clamping an oversized result can bring the request back
  under the trigger on its own. Deciding to clear *before* clamping discards
  history that did not need to go.
"""

import asyncio

from langchain.agents.middleware.context_editing import (
    DEFAULT_TOOL_PLACEHOLDER,
    ClearToolUsesEdit,
)
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.archi.pipelines.agents.tools.result_limits import TRUNCATION_MARKER
from src.archi.pipelines.agents.utils.context_budget import ContextBudget
from src.archi.pipelines.agents.utils.context_middleware import (
    ContextBudgetMiddleware,
    build_context_middleware,
    count_request_tokens,
)

RETRIEVAL = "search_vectorstore_hybrid"


def _budget(**over):
    """A ContextBudget with the fields this layer actually reads."""
    base = dict(
        context_window=32768,
        generation_reserve=4915,
        counting_margin=1638,
        trigger=2000,
        keep=3,
        per_result_tokens=500,
        exempt_floor_tokens=0,
        exempt_count=0,
    )
    base.update(over)
    return ContextBudget(**base)


def _round(name, content, call_id):
    """One AI tool call and its result, the shape `apply` requires for pairing."""
    return [
        AIMessage(
            content="",
            tool_calls=[{"name": name, "args": {"q": "gcc"}, "id": call_id}],
        ),
        ToolMessage(content=content, tool_call_id=call_id, name=name),
    ]


def _thread(rounds):
    """Build a conversation from (tool_name, content) pairs."""
    msgs = [HumanMessage(content="what is the salloc syntax?")]
    for idx, (name, content) in enumerate(rounds):
        msgs.extend(_round(name, content, f"c{idx}"))
    return msgs


def _request(messages, *, system_prompt=None, tools=None, model=None):
    """Construct a ModelRequest.

    All nine fields are required on the pinned 1.0.3 dataclass and there is no
    ``__post_init__``, so this is the whole recipe.
    """
    return ModelRequest(
        model=model if model is not None else _StubModel(),
        system_prompt=system_prompt,
        messages=messages,
        tool_choice=None,
        tools=tools if tools is not None else [],
        response_format=None,
        state={"messages": messages},
        runtime=None,
    )


class _StubModel:
    """A model whose exact tokenizer raises.

    Any counter that reaches for ``get_num_tokens_from_messages`` fails loudly
    here rather than silently taking a tiktoken dependency into the hot path of
    every model call.
    """

    def get_num_tokens_from_messages(self, messages, tools=None):
        raise AssertionError("the counter must not call the model's tokenizer")


class _CappedModel(_StubModel):
    """A model carrying the output cap that actually applies at runtime.

    ``AnthropicProvider`` binds ``max_tokens`` onto the chat model, and that is
    the value the provider enforces — so it, not the declared metadata, is what
    the generation reserve has to be sized against. Claude Sonnet 4's 64000
    against a 200000 window is the case the reserve exists for.
    """

    max_tokens = 64000


def _reduce(middleware, request):
    """Run the sync wrapper, returning the request the handler actually saw."""
    seen = {}

    def handler(req):
        seen["request"] = req
        return AIMessage(content="answer")

    middleware.wrap_model_call(request, handler)
    return seen["request"]


def _placeholders(messages):
    return [
        m
        for m in messages
        if isinstance(m, ToolMessage)
        and m.response_metadata.get("context_editing", {}).get("cleared")
    ]


class TestUpstreamContract:
    """Task 4.1 / 4.11: pin the two upstream behaviours everything rests on."""

    def test_clear_tool_uses_edit_accepts_the_options_used_here(self):
        edit = ClearToolUsesEdit(
            trigger=2000,
            clear_at_least=0,
            keep=3,
            clear_tool_inputs=False,
            exclude_tools=(),
            placeholder="[cleared]",
        )
        assert edit.trigger == 2000
        assert edit.keep == 3
        assert edit.clear_tool_inputs is False

    def test_apply_mutates_the_list_in_place_and_returns_none(self):
        """Assert the mutation, not just the signature.

        If an upgrade switched ``apply`` to returning a new list, a wrapper that
        ignores the return value would silently discard every reduction and the
        bound would quietly stop existing. That must fail here, not in
        production.
        """
        messages = _thread([("t", "X" * 4000)] * 6)
        before = list(messages)

        result = ClearToolUsesEdit(trigger=10, keep=1).apply(
            messages, count_tokens=lambda m: 10_000
        )

        assert result is None, "apply returns None; the mutation is the output"
        assert messages is not before
        assert any(
            new is not old for new, old in zip(messages, before)
        ), "apply must have rebound at least one slot in the list it was given"
        assert _placeholders(messages), "expected cleared results"

    def test_apply_never_changes_the_length_or_order_of_the_list(self):
        """The precondition the index-based restore rests on.

        ``apply`` only ever executes ``messages[idx] = ...``. If an upgrade
        began inserting or removing elements, every restored index would land on
        the wrong message and the wrapper would corrupt the request rather than
        reduce it.
        """
        messages = _thread([("t", "X" * 4000)] * 6)
        before_len = len(messages)
        before_ids = [m.tool_call_id for m in messages if isinstance(m, ToolMessage)]

        ClearToolUsesEdit(trigger=10, keep=1).apply(
            messages, count_tokens=lambda m: 10_000
        )

        assert len(messages) == before_len
        assert [
            m.tool_call_id for m in messages if isinstance(m, ToolMessage)
        ] == before_ids

    def test_the_edit_is_built_with_the_options_the_design_requires(self):
        """Three silent correctness preconditions, pinned at the construction site.

        ``clear_at_least=0`` is the load-bearing one: above zero, ``apply``
        re-measures and breaks early believing it reclaimed enough, counting
        tokens from results the wrapper is about to restore — so the reduction
        stops short and the restore hands the tokens straight back.
        """
        middleware = ContextBudgetMiddleware(budget=_budget(trigger=1234, keep=5))
        edit = middleware.edit

        assert edit.clear_at_least == 0
        assert edit.clear_tool_inputs is False
        assert tuple(edit.exclude_tools) == ()
        assert edit.trigger == 1234
        assert edit.keep == 5

    def test_model_request_exposes_system_prompt(self):
        """A review round asserted this field is named ``system_message``.

        ``dataclasses.fields()`` on the pinned version says otherwise, and
        upstream's own ``wrap_model_call`` reads ``system_prompt``. Pin it so a
        rename fails loudly here rather than at runtime.
        """
        request = _request([HumanMessage(content="hi")], system_prompt="you are archi")

        assert request.system_prompt == "you are archi"
        assert not hasattr(request, "system_message")

    def test_override_leaves_the_original_request_untouched(self):
        original = [HumanMessage(content="hi")]
        request = _request(original)

        replaced = request.override(messages=[HumanMessage(content="bye")])

        assert replaced is not request
        assert request.messages is original
        assert [m.content for m in request.messages] == ["hi"]


class TestCompleteRequestCounting:
    """Tasks 4.2-4.5: the counter measures the request, not just the messages."""

    def test_counter_includes_the_system_prompt(self):
        messages = _thread([("t", "small")])

        without = count_request_tokens(_request(messages))
        with_prompt = count_request_tokens(
            _request(messages, system_prompt="S" * 20_000)
        )

        assert with_prompt > without

    def test_counter_includes_tool_schemas(self):
        from langchain.tools import tool

        @tool
        def search_the_knowledge_base(query: str) -> str:
            """Search the indexed knowledge base for passages relevant to a query."""
            return ""

        messages = _thread([("t", "small")])

        without = count_request_tokens(_request(messages))
        with_tools = count_request_tokens(
            _request(messages, tools=[search_the_knowledge_base])
        )

        assert with_tools > without

    def test_messages_within_budget_but_request_over_budget_reduces(self):
        """The exact case upstream's ``approximate`` counting misses.

        The messages fit. The system prompt pushes the *request* past the
        trigger, and the provider charges for all of it.
        """
        messages = _thread([("t", "X" * 400)] * 5)
        budget = _budget(trigger=1000, keep=1, per_result_tokens=10_000)

        assert (
            count_request_tokens(_request(messages)) < budget.trigger
        ), "precondition: messages alone must fit"
        request = _request(messages, system_prompt="S" * 8_000)
        assert count_request_tokens(request) > budget.trigger

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), request)

        assert _placeholders(reduced.messages), "the complete request was over budget"

    def test_counter_never_calls_the_models_tokenizer(self):
        """A model stub whose tokenizer raises must still count successfully."""
        request = _request(_thread([("t", "X" * 400)]), model=_StubModel())

        assert count_request_tokens(request) > 0

        _reduce(ContextBudgetMiddleware(budget=_budget()), request)


class TestPostReductionRemeasure:
    """Task 4.6: re-measure, and say so in the message string."""

    def test_still_over_budget_logs_the_measured_overage(self, caplog):
        """``setup_logging`` renders ``%(message)s`` only.

        Numbers passed via ``extra={...}`` satisfy ``caplog`` and emit nothing
        at all in production, so the test asserts on the formatted message.
        """
        messages = _thread([("t", "X" * 4000)] * 3)
        budget = _budget(trigger=100, keep=3, per_result_tokens=10_000)

        with caplog.at_level("WARNING"):
            _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings, "expected a warning when the request is still over budget"
        rendered = " ".join(r.getMessage() for r in warnings)
        assert any(
            ch.isdigit() for ch in rendered
        ), f"the overage must be interpolated into the message: {rendered!r}"
        assert "100" in rendered or "budget" in rendered.lower()

    def test_an_empty_message_list_is_passed_straight_through(self):
        """The very first model call, before anything has accumulated."""
        request = _request([])

        reduced = _reduce(ContextBudgetMiddleware(budget=_budget()), request)

        assert reduced is request

    def test_within_budget_after_reduction_logs_no_warning(self, caplog):
        messages = _thread([("t", "small")])

        with caplog.at_level("WARNING"):
            _reduce(ContextBudgetMiddleware(budget=_budget()), _request(messages))

        assert not [r for r in caplog.records if r.levelname == "WARNING"]


class TestSyncAsyncParity:
    """Task 4.7: one shared helper, not two copies that drift."""

    def test_sync_and_async_produce_identical_reductions(self):
        """``pytest-asyncio`` is not a dependency here.

        The project's convention is ``asyncio.run`` inside a sync test — see
        ``test_active_memory_contextvar.py``. An ``async def`` test marked
        ``@pytest.mark.asyncio`` without the plugin is silently not run, which
        would make this parity check a false green.
        """
        budget = _budget(trigger=200, keep=1, per_result_tokens=50)
        rounds = [("t", "X" * 2000)] * 4

        sync_request = _request(_thread(rounds), system_prompt="sys")
        async_request = _request(_thread(rounds), system_prompt="sys")

        sync_out = _reduce(ContextBudgetMiddleware(budget=budget), sync_request)

        seen = {}

        async def handler(req):
            seen["request"] = req
            return AIMessage(content="answer")

        async def _run():
            await ContextBudgetMiddleware(budget=budget).awrap_model_call(
                async_request, handler
            )

        asyncio.run(_run())
        async_out = seen["request"]

        assert [m.content for m in sync_out.messages] == [
            m.content for m in async_out.messages
        ]


class TestUniversalCeiling:
    """Tasks 4.8-4.10: the ceiling applies to every survivor, whatever its tool."""

    def test_preserved_result_from_an_unrelated_tool_is_truncated(self):
        """A stand-in for an MCP or caller-supplied tool.

        ``keep`` selects by recency across all tools, so a per-tool ceiling
        lapses the moment another tool is enabled. Nothing here is clearable —
        all three results are preserved — so the ceiling is the only thing
        standing between this request and the context window.
        """
        budget = _budget(trigger=2000, keep=3, per_result_tokens=200)
        messages = _thread([("mcp_fetch_issue", "X" * 40_000)] * 3)
        request = _request(messages)

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), request)

        results = [m for m in reduced.messages if isinstance(m, ToolMessage)]
        assert not _placeholders(reduced.messages), "precondition: nothing clearable"
        for message in results:
            assert len(message.content) < 40_000
            assert message.content.endswith(TRUNCATION_MARKER)
        assert count_request_tokens(reduced) <= budget.trigger

    def test_exempted_retrieval_result_is_truncated_too(self):
        """Exemption from *clearing* is not exemption from the ceiling."""
        budget = _budget(trigger=2000, keep=1, per_result_tokens=200, exempt_count=2)
        messages = _thread([(RETRIEVAL, "X" * 40_000)] * 3)

        reduced = _reduce(
            ContextBudgetMiddleware(budget=budget, retrieval_tool_name=RETRIEVAL),
            _request(messages),
        )

        retained = [
            m
            for m in reduced.messages
            if isinstance(m, ToolMessage)
            and not m.response_metadata.get("context_editing", {}).get("cleared")
        ]
        assert retained, "the exemption must retain the earliest retrieval results"
        for message in retained:
            assert len(message.content) < 40_000
            assert message.content.endswith(TRUNCATION_MARKER)

    def test_a_result_within_the_ceiling_passes_through_byte_identical(self):
        budget = _budget(trigger=100_000, keep=3, per_result_tokens=500)
        body = "a normal tool result about salloc"
        messages = _thread([("t", body)])

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        result = [m for m in reduced.messages if isinstance(m, ToolMessage)][0]
        assert result.content == body
        assert TRUNCATION_MARKER not in result.content


class TestCeilingIsABackstopNotAnOverride:
    """The ceiling must not re-truncate what the source clamps already sized.

    Both tools clamp their own output to a tuned character limit before the
    result ever reaches a message. A middleware ceiling *below* those limits
    silently overrides the tuning and destroys evidence on every call — and it
    ran unconditionally, so it did so at 1.6% of budget as readily as at 100%.
    """

    def test_ceiling_sits_above_both_source_clamps(self):
        from src.archi.pipelines.agents.tools.local_files import (
            DEFAULT_FETCH_RESULT_CHARS,
        )
        from src.archi.pipelines.agents.tools.retriever import (
            DEFAULT_RETRIEVER_RESULT_CHARS,
        )
        from src.archi.pipelines.agents.utils.context_budget import (
            DEFAULT_PER_RESULT_TOKENS,
        )
        from src.archi.pipelines.agents.utils.context_middleware import CHARS_PER_TOKEN

        largest_source_clamp = max(
            DEFAULT_RETRIEVER_RESULT_CHARS, DEFAULT_FETCH_RESULT_CHARS
        )
        assert DEFAULT_PER_RESULT_TOKENS * CHARS_PER_TOKEN >= largest_source_clamp, (
            "the middleware ceiling is a backstop for tools that do not clamp "
            "themselves; below the source clamps it silently overrides them"
        )

    def test_a_source_clamped_result_survives_reduction_byte_identical(self):
        from src.archi.pipelines.agents.tools.retriever import (
            DEFAULT_RETRIEVER_RESULT_CHARS,
        )
        from src.archi.pipelines.agents.utils.context_budget import (
            DEFAULT_PER_RESULT_TOKENS,
        )

        body = "R" * DEFAULT_RETRIEVER_RESULT_CHARS
        budget = _budget(trigger=126_000, per_result_tokens=DEFAULT_PER_RESULT_TOKENS)

        reduced = _reduce(
            ContextBudgetMiddleware(budget=budget),
            _request(_thread([(RETRIEVAL, body)])),
        )

        result = [m for m in reduced.messages if isinstance(m, ToolMessage)][0]
        assert result.content == body, "a full-size retrieval result lost characters"

    def test_clamping_does_not_run_when_the_request_is_under_budget(self):
        """No pressure, no truncation. The ceiling is pressure-triggered."""
        budget = _budget(trigger=100_000, per_result_tokens=10)
        body = "X" * 40_000

        reduced = _reduce(
            ContextBudgetMiddleware(budget=budget), _request(_thread([("t", body)]))
        )

        result = [m for m in reduced.messages if isinstance(m, ToolMessage)][0]
        assert result.content == body


class TestExemptionIsBestEffort:
    """The exemption yields rather than letting the bound fail.

    At archi's shipped defaults — retrieval call budget 2, keep 3 — a five-result
    turn makes the exempt set *identical* to the clearable candidate set, so an
    unconditional restore undoes every clearing the pass achieved and the request
    goes out over budget with nothing reclaimed.
    """

    @staticmethod
    def _shipped(trigger, exempt_count=2):
        return _budget(
            trigger=trigger, keep=3, per_result_tokens=2100, exempt_count=exempt_count
        )

    def test_exemption_is_shed_when_the_request_is_still_over_budget(self):
        messages = _thread(
            [
                (RETRIEVAL, "R" * 8000),
                (RETRIEVAL, "R" * 8000),
                ("fetch_catalog_document", "F" * 6000),
                ("fetch_catalog_document", "F" * 6000),
                ("fetch_catalog_document", "F" * 6000),
            ]
        )
        budget = self._shipped(trigger=2000)
        request = _request(messages)
        before = count_request_tokens(request)

        reduced = _reduce(
            ContextBudgetMiddleware(budget=budget, retrieval_tool_name=RETRIEVAL),
            request,
        )

        assert _placeholders(reduced.messages), (
            "the exempt set is the entire clearable set here; an unconditional "
            "restore reclaims nothing at all"
        )
        assert count_request_tokens(reduced) < before

    def test_an_exempt_result_apply_never_cleared_cannot_be_shed(self):
        """Shedding gives back what ``apply`` took; it does not clear anew.

        When every exempt result also falls inside the ``keep`` window there is
        nothing to give back, and the loop must run to exhaustion without
        clearing evidence the preserve count is holding.
        """
        messages = _thread([(RETRIEVAL, "R" * 4000)] * 3)
        budget = _budget(trigger=100, keep=3, per_result_tokens=10_000, exempt_count=3)

        reduced = _reduce(
            ContextBudgetMiddleware(budget=budget, retrieval_tool_name=RETRIEVAL),
            _request(messages),
        )

        assert not _placeholders(reduced.messages)
        assert all(
            len(m.content) == 4000
            for m in reduced.messages
            if isinstance(m, ToolMessage)
        )

    def test_exemption_is_kept_when_clearing_alone_gets_under_budget(self):
        """The complement: the exemption yields only when it has to."""
        messages = _thread(
            [
                (RETRIEVAL, "R" * 400),
                (RETRIEVAL, "R" * 400),
                ("fetch_catalog_document", "F" * 40_000),
                ("fetch_catalog_document", "F" * 40_000),
                ("t", "small"),
                ("t", "small"),
                ("t", "small"),
            ]
        )
        budget = self._shipped(trigger=3000)

        reduced = _reduce(
            ContextBudgetMiddleware(budget=budget, retrieval_tool_name=RETRIEVAL),
            _request(messages),
        )

        retrieval = [
            m
            for m in reduced.messages
            if isinstance(m, ToolMessage) and m.name == RETRIEVAL
        ]
        assert len(retrieval) == 2
        for message in retrieval:
            assert not message.response_metadata.get("context_editing", {}).get(
                "cleared"
            ), "retrieval evidence was shed while other results could still go"

    def test_the_newest_exempt_entry_is_shed_first(self):
        """Earliest retrieval results are evidence; later ones trend to refusals.

        The same ordering rationale that makes the *earliest* results exempt
        makes the *newest* of them the cheapest to give up under pressure.
        """
        messages = _thread(
            [
                (RETRIEVAL, "OLDEST" + "R" * 8000),
                (RETRIEVAL, "NEWEST" + "R" * 8000),
                ("fetch_catalog_document", "F" * 6000),
                ("fetch_catalog_document", "F" * 6000),
                ("fetch_catalog_document", "F" * 6000),
            ]
        )
        # Measured for this fixture: 8693 tokens with both exempt results
        # restored, 6702 after shedding the newest, 4711 after shedding both.
        # A trigger between the first two isolates "shed exactly one" — below
        # 4711 the loop correctly sheds everything and proves nothing about
        # order.
        budget = self._shipped(trigger=6800)

        reduced = _reduce(
            ContextBudgetMiddleware(budget=budget, retrieval_tool_name=RETRIEVAL),
            _request(messages),
        )

        retrieval = [
            m
            for m in reduced.messages
            if isinstance(m, ToolMessage) and m.name == RETRIEVAL
        ]
        oldest, newest = retrieval[0], retrieval[1]
        assert not oldest.response_metadata.get("context_editing", {}).get(
            "cleared"
        ), "the earliest retrieval result is the evidence; shed it last"
        assert newest.response_metadata.get("context_editing", {}).get("cleared")


class TestListContentResults:
    """MCP tools return a list of content blocks, not a string.

    ``langchain_mcp_adapters`` returns ``list[str]`` whenever a tool yields two
    or more text blocks, and those tools are merged into the agent's toolset.
    Skipping them exempts precisely the tool class most likely to return an
    unbounded payload — and a naive string clamp raises ``TypeError`` on them.
    """

    @staticmethod
    def _blocks(*sizes):
        return [{"type": "text", "text": "Z" * n} for n in sizes]

    def test_list_content_is_bounded(self):
        budget = _budget(trigger=500, per_result_tokens=200)
        messages = _thread([("mcp_read_file", "placeholder")])
        messages[-1] = ToolMessage(
            content=self._blocks(40_000, 40_000),
            tool_call_id="c0",
            name="mcp_read_file",
        )

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        result = [m for m in reduced.messages if isinstance(m, ToolMessage)][0]
        assert count_request_tokens(_request([result])) <= budget.trigger

    def test_a_list_of_plain_strings_is_bounded(self):
        """The shape ``langchain_mcp_adapters`` actually returns.

        Its annotation is ``str | list[str]`` — bare strings, not content-block
        dicts — so this is the production MCP path, and covering only the dict
        form would leave the real one unbounded.
        """
        budget = _budget(trigger=500, per_result_tokens=200)
        messages = _thread([("mcp_read_file", "placeholder")])
        messages[-1] = ToolMessage(
            content=["Z" * 40_000, "Y" * 40_000],
            tool_call_id="c0",
            name="mcp_read_file",
        )

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        result = [m for m in reduced.messages if isinstance(m, ToolMessage)][0]
        assert count_request_tokens(_request([result])) <= budget.trigger
        assert isinstance(result.content, list)
        assert all(isinstance(block, str) for block in result.content)

    def test_content_with_no_clampable_text_is_left_alone(self):
        """Better an unclamped result than a corrupted one.

        ``model_copy`` performs no validation, so writing a malformed
        replacement would surface much later inside the provider adapter with
        nothing left to connect it to this code.
        """
        budget = _budget(trigger=100, per_result_tokens=5)
        blocks = [{"type": "image", "source": {"data": "z" * 40_000}}]
        messages = _thread([("mcp_screenshot", "placeholder")])
        messages[-1] = ToolMessage(
            content=blocks, tool_call_id="c0", name="mcp_screenshot"
        )

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        result = [m for m in reduced.messages if isinstance(m, ToolMessage)][0]
        assert result.content == blocks

    def test_list_content_keeps_its_block_structure(self):
        """Truncate the text inside a block; never the block structure itself."""
        budget = _budget(trigger=500, per_result_tokens=200)
        messages = _thread([("mcp_read_file", "placeholder")])
        messages[-1] = ToolMessage(
            content=self._blocks(40_000, 40_000),
            tool_call_id="c0",
            name="mcp_read_file",
        )

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        result = [m for m in reduced.messages if isinstance(m, ToolMessage)][0]
        assert isinstance(result.content, list)
        assert all(isinstance(block, dict) for block in result.content)
        assert all(block.get("type") == "text" for block in result.content)


class TestFailsOpen:
    """A middleware that exists to prevent a failure must not cause one."""

    def test_reduce_returns_the_request_unchanged_when_counting_raises(self, caplog):
        """LangGraph composes model handlers with no ``try``.

        Anything raised here propagates and kills the user's turn — for a
        middleware whose whole job is preventing a failure, that trade is
        backwards. Failing open costs only the reactive overflow handler, which
        is still in place.
        """
        from decimal import Decimal

        messages = _thread([("t", "X" * 40_000)] * 4)
        # A provider-native tool dict carrying a value json.dumps cannot encode.
        request = _request(messages, tools=[{"name": "t", "cost": Decimal("1.0")}])

        with caplog.at_level("ERROR"):
            reduced = _reduce(ContextBudgetMiddleware(budget=_budget()), request)

        assert reduced is request, "must hand the model the original request"
        assert [r for r in caplog.records if r.levelname == "ERROR"]


class TestOrdering:
    """Task 4.12: clamp before deciding to clear."""

    def test_clamping_the_newest_result_spares_the_older_ones(self):
        """One oversized newest result, several older ones that fit once clamped.

        Deciding to clear first would count the oversized result, blow the
        trigger, and discard history that never needed to go.
        """
        budget = _budget(trigger=1500, keep=3, per_result_tokens=100)
        messages = _thread(
            [
                ("t", "OLDEST evidence worth keeping"),
                ("t", "SECOND evidence worth keeping"),
                ("t", "third"),
                ("t", "fourth"),
                ("t", "X" * 40_000),
            ]
        )

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        assert not _placeholders(
            reduced.messages
        ), "clamping alone brought the request under the trigger"
        contents = [m.content for m in reduced.messages if isinstance(m, ToolMessage)]
        assert "OLDEST evidence worth keeping" in contents
        assert "SECOND evidence worth keeping" in contents


class TestStateIsolation:
    """Task 4.13: reduction never reaches conversation state."""

    def test_reduction_does_not_write_placeholders_into_state(self):
        budget = _budget(trigger=100, keep=1, per_result_tokens=10_000)
        messages = _thread([("t", "X" * 4000)] * 4)
        request = _request(messages)
        original_lengths = [
            len(m.content) for m in messages if isinstance(m, ToolMessage)
        ]

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), request)

        assert _placeholders(reduced.messages), "precondition: the view was reduced"
        assert request.messages is messages, "the request's own list must be untouched"
        assert [
            len(m.content) for m in messages if isinstance(m, ToolMessage)
        ] == original_lengths
        assert not _placeholders(messages), "state must retain the original results"

    def test_a_following_turn_is_not_served_placeholder_content(self):
        """The defect this guards is permanent, not transient.

        Writing into state means every later turn inherits the placeholders, so
        a second reduction over the same state must see the same input as the
        first.
        """
        budget = _budget(trigger=100, keep=1, per_result_tokens=10_000)
        messages = _thread([("t", "X" * 4000)] * 4)
        request = _request(messages)
        middleware = ContextBudgetMiddleware(budget=budget)

        first = _reduce(middleware, request)
        second = _reduce(middleware, request)

        assert first.messages is not second.messages, (
            "each call must reduce its own copy; handing the model the list that "
            "lives in state is what makes the loss permanent"
        )
        assert [m.content for m in first.messages] == [
            m.content for m in second.messages
        ], "the second turn must start from the same unreduced state as the first"
        assert not _placeholders(messages), "state must survive both calls intact"


class TestClearedPlaceholder:
    """Task 5.5: what a cleared result actually says to the model.

    The wording is pinned against ``spec.md:318``, which requires the
    placeholder state the result was cleared **to stay within the context
    window** and direct the model not to re-request it — not against taste. A
    legitimate-sounding reword ("to fit context", "do not request it again")
    fails these assertions on purpose; change the spec first.
    """

    def test_a_cleared_result_carries_an_instructive_placeholder(self):
        """Assert on the message that came out of a real reduction.

        Not on the constant: ``DEFAULT_PLACEHOLDER`` binds into the constructor
        signature at ``def`` time, so monkeypatching it is a no-op and a test
        that reads it back proves only that a module attribute exists. This
        runs a reduction and reads what the model would be handed.
        """
        middleware = ContextBudgetMiddleware(budget=_budget(trigger=200))

        reduced = _reduce(middleware, _request(_thread([("t", "X" * 4000)] * 6)))

        cleared = _placeholders(reduced.messages)
        assert cleared, "precondition: the pass must have cleared something"
        text = cleared[0].content
        assert isinstance(text, str), "upstream replaces the content wholesale"
        assert (
            text != DEFAULT_TOOL_PLACEHOLDER
        ), "upstream's bare '[cleared]' is exactly what this replaces"
        lowered = text.lower()
        assert "clear" in lowered, "the model must learn the content was removed"
        assert "context window" in lowered, "spec.md:318 — why it was removed"
        assert "re-request" in lowered, "spec.md:318 — and that retrying will not help"


class TestBuiltFromConfig:
    """Group 5: the construction path, from config to the edit's options.

    Every assertion here is on a middleware the **factory** produced. The
    constructor-level pins in ``TestUpstreamContract`` cannot reach this: handing
    ``ContextBudget(trigger=1234)`` to the constructor and reading ``1234`` back
    tests that a dataclass stores its argument. What has to hold is that the
    numbers were *derived* — from a context window, an output cap, a config
    block and the runtime tool budgets — and derivation is only observable from
    the entry point that does it.
    """

    def test_the_trigger_is_the_derived_budget_not_the_raw_window(self):
        """Task 5.1. The raw window handed through is the defect this pins."""
        built = build_context_middleware(model=_StubModel(), context_window=200_000)

        assert len(built) == 1
        # 200000 - 15% generation reserve - 5% counting margin.
        assert built[0].edit.trigger == 160_000
        # Both fields, because they are read by different code: the edit's own
        # gate uses ``edit.trigger``, while this wrapper's early return and its
        # shed loop read ``budget.trigger``. A middleware with a correct edit and
        # a raw-window budget installs no effective bound.
        assert built[0].budget.trigger == 160_000

    def test_the_effective_output_cap_reaches_the_trigger(self):
        """Task 5.1, the half that matters most.

        A factory that never calls ``resolve_output_cap`` still produces a
        plausible-looking 160000 and passes the test above. Claude Sonnet 4 is
        the regression case: a 64000 cap against a 200000 window means a 160000
        prompt budget is rejected before the trigger is ever consulted.
        """
        built = build_context_middleware(model=_CappedModel(), context_window=200_000)

        # reserve = max(15% = 30000, the bound cap 64000) = 64000.
        assert built[0].edit.trigger == 126_000

    def test_keep_defaults_to_three(self):
        """Task 5.2, the default half."""
        built = build_context_middleware(model=_StubModel(), context_window=200_000)

        # The literal 3, never ``DEFAULT_KEEP``: asserting a module constant
        # against the constant that produced it passes whatever it is changed to.
        assert built[0].edit.keep == 3

    def test_a_configured_keep_reaches_the_edit(self):
        """Task 5.2, the override half — the whole config-to-edit chain."""
        built = build_context_middleware(
            model=_StubModel(),
            context_window=200_000,
            config={"services": {"chat_app": {"context_editing": {"keep": 7}}}},
        )

        assert built[0].edit.keep == 7

    def test_a_pipeline_keep_overrides_the_service_layer(self):
        """Task 5.2: the per-pipeline layer must arrive, not just the service one.

        A factory that forgets to forward ``pipeline_config`` passes the test
        above and fails only here.
        """
        built = build_context_middleware(
            model=_StubModel(),
            context_window=200_000,
            config={"services": {"chat_app": {"context_editing": {"keep": 7}}}},
            pipeline_config={"context_editing": {"keep": 9}},
        )

        assert built[0].edit.keep == 9

    def test_the_exemption_is_a_count_here_never_upstreams_name_list(self):
        """Task 5.3: ``exclude_tools`` stays empty; the count arrives instead.

        Upstream's option exempts *every* message bearing the name, which cannot
        express the count-bounded exemption the budget sizes its worst case
        from. Asserting both would be asserting a contradiction.
        """
        built = build_context_middleware(
            model=_StubModel(),
            context_window=200_000,
            tool_budgets={RETRIEVAL: 2},
        )

        assert tuple(built[0].edit.exclude_tools) == ()
        assert built[0].budget.exempt_count == 2

    def test_the_exemption_follows_the_configured_tool_name(self):
        """Task 5.3: the name is a parameter, and it must actually be used.

        ``RETRIEVAL`` equals the constructor's own default, so a test using it
        cannot tell a forwarded name from an ignored one. This one would fail
        both for a factory that drops ``retrieval_tool_name`` on the way to the
        wrapper and for one that looks the budget up under the hardcoded
        default. The divergence is real: ``create_retriever_tool``'s own default
        name is ``search_knowledge_base``.
        """
        built = build_context_middleware(
            model=_StubModel(),
            context_window=200_000,
            tool_budgets={"search_knowledge_base": 2},
            retrieval_tool_name="search_knowledge_base",
        )

        assert built[0].retrieval_tool_name == "search_knowledge_base"
        assert built[0].budget.exempt_count == 2

    def test_an_unaffordable_exemption_is_dropped_by_the_shared_guard(self):
        """Task 5.1/5.3: pins the *delegation*, not two arithmetic results.

        A factory that inlines ``window - max(window*0.15, cap) - window*0.05``
        reproduces every trigger asserted above exactly, while silently skipping
        ``resolve_budget``'s irreducible-floor guard — so the exemption is never
        dropped and becomes the second unbounded floor the design forbids. At
        this window the guard is the only thing that separates the two.
        """
        built = build_context_middleware(
            model=_StubModel(),
            context_window=32_768,
            tool_budgets={RETRIEVAL: 2},
        )

        # trigger 26215; the exemption plus the preserved results would hold
        # (2 + 3) x 2100 = 10500, above the 8738 that is a third of the budget.
        assert built[0].budget.trigger == 26_215
        assert built[0].budget.exempt_count == 0

    def test_the_edit_retains_the_record_of_the_models_own_call(self):
        """Task 5.4, asserted behaviourally rather than as a flag.

        With the arguments cleared too the model has no record of the call, so
        it re-fetches the same document and spins to the recursion limit —
        converting an overflow into a timeout. ``spec.md:369`` requires the
        arguments survive; nothing asserted it before.
        """
        built = build_context_middleware(model=_StubModel(), context_window=1_000)
        assert built[0].edit.clear_tool_inputs is False

        reduced = _reduce(built[0], _request(_thread([("t", "X" * 4000)] * 6)))

        assert _placeholders(reduced.messages), "precondition: something was cleared"
        calls = [
            m.tool_calls[0]
            for m in reduced.messages
            if isinstance(m, AIMessage) and m.tool_calls
        ]
        assert calls, "the assistant messages must still carry their tool calls"
        assert all(c["args"] == {"q": "gcc"} for c in calls)

    def test_the_placeholder_is_not_a_call_site_decision(self):
        """Task 5.5 through the production path.

        The wording is a design decision, not a knob: a ``placeholder``
        parameter on the factory would let a call site silently reinstate an
        uninformative marker, and the constructor-level test cannot see that
        because it never runs the factory.
        """
        built = build_context_middleware(model=_StubModel(), context_window=1_000)

        reduced = _reduce(built[0], _request(_thread([("t", "X" * 4000)] * 6)))

        cleared = _placeholders(reduced.messages)
        assert cleared, "precondition: the pass must have cleared something"
        lowered = cleared[0].content.lower()
        assert lowered != DEFAULT_TOOL_PLACEHOLDER
        assert "context window" in lowered
        assert "re-request" in lowered

    def test_no_middleware_is_built_when_the_window_is_undeterminable(self):
        """The fail-open contract: a list, always, so the call site has no logic.

        This is not a hypothetical branch. ``_get_model_context_window`` matches
        the configured model against a provider's hardcoded ``ModelInfo`` list,
        and returns None for anything absent from it.
        """
        assert build_context_middleware(model=_StubModel(), context_window=None) == []

    def test_no_middleware_is_built_when_the_operator_disables_it(self):
        """The same contract, reached through ``enabled: false``.

        The positive control is what gives this teeth: without it, a factory
        that bailed on *any* non-empty config would pass. Note that only a real
        YAML boolean disables the bound — ``enabled: 0`` is an invalid value,
        and an invalid value never silently removes the protection.
        """
        block = {"services": {"chat_app": {"context_editing": {"enabled": False}}}}
        assert (
            build_context_middleware(
                model=_StubModel(), context_window=200_000, config=block
            )
            == []
        )

        block["services"]["chat_app"]["context_editing"]["enabled"] = True
        assert (
            len(
                build_context_middleware(
                    model=_StubModel(), context_window=200_000, config=block
                )
            )
            == 1
        )


class TestDeclaredContextWindow:
    """Task 7A: an operator-declared window, and an audible failure without one.

    These are the tests that would have caught the real defect. Everything in
    ``TestBuiltFromConfig`` supplies a window directly, which is exactly what
    production cannot do: the provider matches the configured model name against
    a list compiled into it, and this deployment's models are not on any such
    list.
    """

    def test_a_declared_window_installs_a_limit_where_metadata_reports_none(self):
        built = build_context_middleware(
            model=_StubModel(),
            context_window=None,
            config={
                "services": {
                    "chat_app": {"context_editing": {"context_window": 32_768}}
                }
            },
        )

        assert len(built) == 1
        assert built[0].budget.trigger == 26_215

    def test_a_declared_window_takes_precedence_over_the_derived_one(self):
        built = build_context_middleware(
            model=_StubModel(),
            context_window=200_000,
            config={
                "services": {
                    "chat_app": {"context_editing": {"context_window": 32_768}}
                }
            },
        )

        assert built[0].budget.trigger == 26_215, "the declared window must win"

    def test_an_invalid_declared_window_falls_back_to_the_derived_one(self):
        """A typo must not cost the protection the derived window provides."""
        built = build_context_middleware(
            model=_StubModel(),
            context_window=200_000,
            config={
                "services": {"chat_app": {"context_editing": {"context_window": "big"}}}
            },
        )

        assert built[0].budget.trigger == 160_000

    def test_an_uninstallable_limit_names_the_model_responsible(self, caplog):
        """Failing open is correct; failing open *silently* is the defect.

        With nothing logged, a deployment protecting nothing is indistinguishable
        from a healthy one — which is how this went unnoticed until the resolver
        was run against the real config.
        """
        built = build_context_middleware(
            model=_StubModel(),
            context_window=None,
            model_label="local/palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4",
        )

        assert built == []
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings, "an installed-nothing outcome must be visible in the logs"
        assert any("Qwen3.6" in r.message for r in warnings)
        assert any("context_window" in r.message for r in warnings), "name the remedy"

    def test_disabling_the_limit_deliberately_is_not_a_warning(self, caplog):
        """Only an *unintended* absence is worth warning about.

        An operator who switched it off does not need to be told so on every
        agent build, and a warning that fires when nothing is wrong trains
        people to ignore it.
        """
        built = build_context_middleware(
            model=_StubModel(),
            context_window=200_000,
            config={"services": {"chat_app": {"context_editing": {"enabled": False}}}},
        )

        assert built == []
        assert not [r for r in caplog.records if r.levelname == "WARNING"]


# --- Group 8: the acceptance criteria ---------------------------------------
#
# Everything above tests a mechanism. These state the outcomes the change is
# *for*, in the terms the spec uses, and they measure rather than infer: the
# assertion is the post-reduction size of the complete request, not that
# clearing happened to occur.


def _is_cleared(message):
    return bool(
        isinstance(message, ToolMessage)
        and message.response_metadata.get("context_editing", {}).get("cleared")
    )


def _tool_results(messages):
    return [m for m in messages if isinstance(m, ToolMessage)]


def _respond(middleware, request):
    """Run the wrapper, returning what it hands back to the caller."""
    return middleware.wrap_model_call(request, lambda req: AIMessage(content="answer"))


class TestTheBoundHolds:
    """8.1-8.5: the request that leaves this layer fits the budget."""

    def test_the_complete_request_is_within_budget_after_reduction(self):
        """8.2: the bound itself, measured — not 'clearing occurred'.

        The system prompt and tool schemas are counted here because they are
        sent on every call; a wrapper measuring only the messages declares a
        request within budget that the provider then rejects.
        """
        # Sized so the two counters diverge. Clamping alone brings the messages
        # under the trigger, so a wrapper counting only them stops there and
        # sends a request the prefix pushes back over. Counting the whole thing
        # keeps clearing until the whole thing fits.
        messages = _thread([("read_doc", "X" * 6000)] * 10)
        request = _request(
            messages,
            system_prompt="S" * 8000,
            tools=[{"name": "read_doc", "description": "D" * 2000}],
        )
        budget = _budget(trigger=6000, keep=3, per_result_tokens=500)

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), request)

        assert count_request_tokens(reduced) <= budget.trigger

    def test_the_oldest_tool_results_are_the_ones_reduced(self):
        """8.1: age decides. Clearing the newest would discard the results the
        model is reasoning about while keeping what it has finished with."""
        messages = _thread([("read_doc", "X" * 6000)] * 6)
        budget = _budget(trigger=2000, keep=3, per_result_tokens=500)

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        flags = [_is_cleared(m) for m in _tool_results(reduced.messages)]
        assert any(flags), "nothing was reduced, so this asserts nothing"
        assert flags[-3:] == [False, False, False], "the preserved three were cleared"
        # Every cleared result precedes every retained one.
        assert flags == sorted(flags, reverse=True)

    def test_a_request_within_budget_is_passed_through_untouched(self):
        """8.5: the same message objects, not merely equal ones."""
        messages = _thread([("read_doc", "small")] * 3)
        budget = _budget(trigger=100_000)

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        assert reduced.messages is messages
        assert not _placeholders(reduced.messages)

    def test_non_reducible_content_over_budget_does_not_raise(self):
        """8.4: a system prompt larger than the whole budget.

        Nothing this layer can clear will bring the request under, so it clears
        what it can and hands the call on. The reactive overflow handler covers
        the remainder; raising here would turn a degraded answer into no answer.
        """
        messages = _thread([("read_doc", "X" * 6000)] * 4)
        request = _request(messages, system_prompt="S" * 40_000)
        budget = _budget(trigger=2000, keep=3, per_result_tokens=500)

        response = _respond(ContextBudgetMiddleware(budget=budget), request)

        assert response.content == "answer"

    def test_a_heavy_document_reading_run_still_reaches_the_model(self):
        """8.6: the boundary criterion — many reads still produce an answer.

        The canned overflow apology is the outcome this change exists to stop
        being routine; here the call completes and the model answers.
        """
        messages = _thread([("read_doc", "X" * 8000)] * 20)
        budget = _budget(trigger=4000, keep=3, per_result_tokens=500)

        response = _respond(ContextBudgetMiddleware(budget=budget), _request(messages))

        assert response.content == "answer"


class TestTheResidual:
    """8.3: what clearing cannot remove, measured rather than assumed."""

    def test_many_small_rounds_leave_a_residue_that_is_reported_not_raised(
        self, caplog
    ):
        """Clearing replaces content; it does not delete the message.

        Each cleared round still costs the AI message framing, the retained
        tool-call arguments, and the placeholder text. Enough rounds and that
        residue alone crosses the budget with nothing left to clear — the case
        that says whether removing whole paired rounds would ever be needed.
        """
        messages = _thread([("read_doc", "small result")] * 200)
        budget = _budget(trigger=1500, keep=3, per_result_tokens=500)

        with caplog.at_level("WARNING"):
            reduced = _reduce(
                ContextBudgetMiddleware(budget=budget), _request(messages)
            )

        assert count_request_tokens(reduced) > budget.trigger, (
            "this scenario is meant to remain over budget; if it no longer does, "
            "the residue shrank and the numbers below need re-measuring"
        )
        rendered = " ".join(
            r.getMessage() for r in caplog.records if r.levelname == "WARNING"
        )
        assert "after reduction" in rendered, (
            "an unreachable budget must be reported, not silently declared met: "
            f"{rendered!r}"
        )

    def test_the_measured_residue_per_cleared_round(self):
        """The number itself, pinned so a placeholder or framing change moves it.

        Measured on the pinned langchain-core: a cleared round costs ~31 tokens
        for the placeholder plus the AI message carrying the tool call and its
        arguments. This test records the figure the design's 'is clearing
        enough?' question turns on.
        """
        budget = _budget(trigger=1, keep=0, per_result_tokens=500)
        one = _thread([("read_doc", "X" * 6000)])
        many = _thread([("read_doc", "X" * 6000)] * 11)

        reduced_one = _reduce(ContextBudgetMiddleware(budget=budget), _request(one))
        reduced_many = _reduce(ContextBudgetMiddleware(budget=budget), _request(many))

        per_round = (
            count_request_tokens(reduced_many) - count_request_tokens(reduced_one)
        ) / 10
        assert 25 <= per_round <= 60, f"residue per cleared round moved: {per_round}"


class TestPreservationAndExemption:
    """8.7-8.9: what survives, and that surviving is still bounded."""

    def test_preserved_results_keep_their_content_within_the_ceiling(self):
        """8.7: preservation exempts from *clearing*, never from the ceiling."""
        small = "a readable result"
        messages = _thread(
            [("read_doc", "X" * 6000)] * 4 + [("read_doc", small)] * 3,
        )
        budget = _budget(trigger=2000, keep=3, per_result_tokens=500)

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        preserved = _tool_results(reduced.messages)[-3:]
        assert [m.content for m in preserved] == [small] * 3

    def test_an_oversized_preserved_result_is_truncated_not_cleared(self):
        """8.7, the other half: over the ceiling it keeps its truncated form,
        which still carries content — unlike a placeholder, which carries none."""
        messages = _thread([("read_doc", "X" * 6000)] * 5)
        budget = _budget(trigger=2000, keep=3, per_result_tokens=500)

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        newest = _tool_results(reduced.messages)[-1]
        assert not _is_cleared(newest)
        assert newest.content.startswith("X")
        assert newest.content.endswith(TRUNCATION_MARKER)
        assert len(newest.content) <= budget.per_result_tokens * 4

    def test_retrieval_evidence_survives_however_old_it_is(self):
        """8.8: the grounding evidence is the oldest thing in the thread and the
        thing the answer cites; recency-based preservation protects neither."""
        messages = _thread(
            [(RETRIEVAL, "R" * 1200)] * 2 + [("read_doc", "X" * 6000)] * 8,
        )
        budget = _budget(trigger=3000, keep=3, per_result_tokens=500, exempt_count=2)

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        evidence = [m for m in _tool_results(reduced.messages) if m.name == RETRIEVAL]
        assert [_is_cleared(m) for m in evidence] == [False, False]

    def test_with_the_exemption_dropped_retrieval_clears_and_the_bound_holds(self):
        """8.9: raising the caps past what the budget can afford makes retrieval
        clearable like anything else — the bound wins over the exemption."""
        messages = _thread([(RETRIEVAL, "R" * 6000)] * 10)
        # keep=3 at the 500-token ceiling is 1500 tokens the bound cannot touch,
        # so the trigger must leave room for those plus the cleared rounds' residue.
        budget = _budget(trigger=3000, keep=3, per_result_tokens=500, exempt_count=0)

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        assert any(_is_cleared(m) for m in _tool_results(reduced.messages))
        assert count_request_tokens(reduced) <= budget.trigger


class TestStructuralIntegrity:
    """8.10-8.11: the reduced request is still a valid one, on every call."""

    def test_no_tool_result_is_left_without_its_originating_call(self):
        """8.10: a ToolMessage whose tool_call_id names no call is rejected by
        the provider — a reduction that broke pairing would fail the request it
        was protecting."""
        messages = _thread([("read_doc", "X" * 6000)] * 8)
        budget = _budget(trigger=2000, keep=3, per_result_tokens=500)

        reduced = _reduce(ContextBudgetMiddleware(budget=budget), _request(messages))

        call_ids = {
            call["id"]
            for m in reduced.messages
            if isinstance(m, AIMessage)
            for call in (m.tool_calls or [])
        }
        orphans = [
            m.tool_call_id
            for m in _tool_results(reduced.messages)
            if m.tool_call_id not in call_ids
        ]
        assert orphans == []

    def test_reduction_happens_on_the_call_that_first_exceeds_the_budget(self):
        """8.11: the loop grows the thread between calls, so a bound applied
        only before the loop protects nothing once the loop is running."""
        middleware = ContextBudgetMiddleware(
            budget=_budget(trigger=2000, keep=3, per_result_tokens=500)
        )
        early = _thread([("read_doc", "small")] * 2)
        later = _thread([("read_doc", "X" * 6000)] * 8)

        first = _reduce(middleware, _request(early))
        second = _reduce(middleware, _request(later))

        assert not _placeholders(first.messages), "reduced a request under budget"
        assert _placeholders(second.messages), "the same instance must reduce later"
