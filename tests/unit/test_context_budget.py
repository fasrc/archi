"""
Unit tests for the in-loop context budget arithmetic (issue #235).

This is the layer that decides *how many tokens a request may send* and with
what settings the trimming machinery should run. It talks to no model and edits
no messages; it produces numbers, and every one of them has a way to be wrong:

* ``ModelInfo.context_window`` is a **total** sequence length covering prompt
  and generation, so a budget equal to the window is exceeded by any answer.
* The declared ``max_output_tokens`` is **not** the effective cap. Anthropic
  applies it only when the caller set no ``max_tokens``; ``LocalProvider``
  declares 8192 and passes it to neither constructor. Sizing from metadata is
  wrong in both directions.
* A reserve fully spent on the answer leaves nothing to absorb token-counting
  error, and the provider rejects that call before any later re-evaluation can
  correct it — so the counting margin is a separate term, not a share of the
  reserve.
* The retrieval exemption is sized ``call_budget x per_result_ceiling``. Both
  terms must be in the budget's own unit; multiplying a character limit and
  comparing it to a token budget reads as cheap while costing several times
  its share.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.archi.pipelines.agents.utils.context_budget import (
    DEFAULT_COUNTING_MARGIN_FRACTION,
    DEFAULT_EXEMPTION_FRACTION,
    DEFAULT_GENERATION_RESERVE_FRACTION,
    DEFAULT_KEEP,
    ContextEditingSettings,
    read_settings,
    resolve_budget,
    resolve_output_cap,
    select_exempt_indices,
)

WINDOW = 32768


def _settings(**over):
    base = dict(
        enabled=True,
        reserve_fraction=DEFAULT_GENERATION_RESERVE_FRACTION,
        margin_fraction=DEFAULT_COUNTING_MARGIN_FRACTION,
        keep=DEFAULT_KEEP,
        per_result_tokens=1200,
        exemption_fraction=DEFAULT_EXEMPTION_FRACTION,
    )
    base.update(over)
    return ContextEditingSettings(**base)


class TestBudgetArithmetic:
    """Task 3.1: budget = window - generation_reserve - counting_margin."""

    def test_budget_subtracts_both_reserve_and_margin(self):
        b = resolve_budget(context_window=WINDOW, output_cap=None, settings=_settings())

        expected_reserve = int(WINDOW * DEFAULT_GENERATION_RESERVE_FRACTION)
        expected_margin = int(WINDOW * DEFAULT_COUNTING_MARGIN_FRACTION)
        assert b.generation_reserve == expected_reserve
        assert b.counting_margin == expected_margin
        assert b.trigger == WINDOW - expected_reserve - expected_margin

    def test_margin_is_not_carved_out_of_the_reserve(self):
        """They are independent terms; the reserve must stay whole."""
        b = resolve_budget(context_window=WINDOW, output_cap=None, settings=_settings())
        assert b.generation_reserve + b.counting_margin < WINDOW
        assert b.trigger < WINDOW - b.generation_reserve

    def test_a_bigger_window_yields_a_bigger_budget(self):
        small = resolve_budget(
            context_window=8000, output_cap=None, settings=_settings()
        )
        large = resolve_budget(
            context_window=200000, output_cap=None, settings=_settings()
        )
        assert large.trigger > small.trigger


class TestGenerationReserve:
    """Tasks 3.9-3.10: the reserve tracks the *effective* output cap."""

    def test_output_cap_larger_than_the_percentage_wins(self):
        """Sonnet 4: 200K window, 64K cap. 15% would leave 170K + 64K = 234K."""
        b = resolve_budget(
            context_window=200000, output_cap=64000, settings=_settings()
        )

        assert b.generation_reserve >= 64000
        assert b.trigger + 64000 <= 200000, "prompt + permitted generation must fit"

    def test_percentage_is_the_floor_when_no_cap_applies(self):
        b = resolve_budget(context_window=WINDOW, output_cap=None, settings=_settings())
        assert b.generation_reserve == int(WINDOW * DEFAULT_GENERATION_RESERVE_FRACTION)

    def test_a_small_cap_does_not_shrink_the_reserve_below_the_floor(self):
        b = resolve_budget(context_window=WINDOW, output_cap=100, settings=_settings())
        assert b.generation_reserve == int(WINDOW * DEFAULT_GENERATION_RESERVE_FRACTION)


class TestEffectiveOutputCap:
    """Task 3.10: a configured cap beats declared metadata, in both directions."""

    def test_configured_cap_on_the_bound_model_wins(self):
        model = type("M", (), {"max_tokens": 120000})()
        assert resolve_output_cap(model, declared_cap=64000) == 120000

    def test_declared_metadata_is_the_fallback(self):
        model = type("M", (), {})()
        assert resolve_output_cap(model, declared_cap=64000) == 64000

    def test_metadata_a_provider_never_applies_is_still_the_fallback(self):
        """LocalProvider declares 8192 but passes it to neither constructor.

        The helper cannot know that, so it reports the declared value; the
        caller passes ``declared_cap=None`` for providers that do not apply it.
        This asserts the contract, not a guess about the provider.
        """
        model = type("M", (), {"max_tokens": None})()
        assert resolve_output_cap(model, declared_cap=None) is None

    def test_non_numeric_configured_cap_is_ignored(self):
        model = type("M", (), {"max_tokens": "many"})()
        assert resolve_output_cap(model, declared_cap=64000) == 64000


class TestFailOpen:
    """Task 3.2: anything unknown or impossible installs nothing."""

    @pytest.mark.parametrize("window", [None, 0, -1, "32768", 3.5])
    def test_unusable_window_yields_no_budget(self, window):
        assert (
            resolve_budget(context_window=window, output_cap=None, settings=_settings())
            is None
        )

    def test_reserve_plus_margin_consuming_the_window_yields_no_budget(self):
        b = resolve_budget(context_window=1000, output_cap=5000, settings=_settings())
        assert b is None

    def test_disabled_yields_no_budget(self):
        b = resolve_budget(
            context_window=WINDOW, output_cap=None, settings=_settings(enabled=False)
        )
        assert b is None


class TestSettingsLookup:
    """Task 3.3-3.5: the established three-layer config lookup."""

    def test_defaults_when_nothing_is_configured(self):
        s = read_settings({}, {})
        assert s.enabled is True
        assert s.keep == DEFAULT_KEEP
        assert s.reserve_fraction == DEFAULT_GENERATION_RESERVE_FRACTION

    def test_chat_app_layer_overrides_defaults(self):
        s = read_settings(
            {"services": {"chat_app": {"context_editing": {"keep": 7}}}}, {}
        )
        assert s.keep == 7

    def test_pipeline_layer_overrides_chat_app(self):
        s = read_settings(
            {"services": {"chat_app": {"context_editing": {"keep": 7}}}},
            {"context_editing": {"keep": 9}},
        )
        assert s.keep == 9

    def test_enabled_false_is_read(self):
        s = read_settings(
            {"services": {"chat_app": {"context_editing": {"enabled": False}}}}, {}
        )
        assert s.enabled is False

    @pytest.mark.parametrize(
        "bad_block",
        [
            {"keep": "three"},
            {"keep": -1},
            {"reserve_fraction": "lots"},
            {"reserve_fraction": 1.5},
            {"reserve_fraction": -0.2},
            {"exemption_fraction": 2.0},
            {"per_result_tokens": 0},
        ],
    )
    def test_invalid_values_fall_back_without_disabling_the_bound(
        self, bad_block, caplog
    ):
        """Task 3.4: warn, use the default for that value, still install."""
        s = read_settings(
            {"services": {"chat_app": {"context_editing": bad_block}}}, {}
        )

        assert s.enabled is True, "an invalid value must not disable the bound"
        assert s.keep == DEFAULT_KEEP or "keep" not in bad_block
        assert 0 < s.reserve_fraction < 1
        assert 0 < s.exemption_fraction <= 1
        assert s.per_result_tokens > 0


class TestExemptionSizing:
    """Tasks 3.6-3.8: the exemption floor, in the budget's own unit."""

    def test_floor_is_call_budget_times_the_token_ceiling(self):
        s = _settings(per_result_tokens=1000)
        b = resolve_budget(
            context_window=WINDOW, output_cap=None, settings=s, retrieval_call_budget=2
        )

        assert b.exempt_floor_tokens == 2 * 1000

    def test_exemption_retained_when_the_floor_is_small(self):
        s = _settings(per_result_tokens=1000, exemption_fraction=1 / 3)
        b = resolve_budget(
            context_window=WINDOW, output_cap=None, settings=s, retrieval_call_budget=2
        )

        assert b.exempt_count == 2, "2000 tokens is well under a third of the budget"

    def test_exemption_dropped_when_the_floor_is_too_large(self, caplog):
        """Task 3.7: fail toward the bound holding, with a warning."""
        s = _settings(per_result_tokens=6000, exemption_fraction=1 / 3)
        b = resolve_budget(
            context_window=WINDOW, output_cap=None, settings=s, retrieval_call_budget=4
        )

        assert b.exempt_count == 0
        assert any("exempt" in r.message.lower() for r in caplog.records)

    def test_raising_the_call_budget_alone_flips_the_exemption_off(self):
        """Task 3.8: the check must track the runtime value, not a constant."""
        s = _settings(per_result_tokens=2000, exemption_fraction=1 / 3)

        low = resolve_budget(
            context_window=WINDOW, output_cap=None, settings=s, retrieval_call_budget=2
        )
        high = resolve_budget(
            context_window=WINDOW, output_cap=None, settings=s, retrieval_call_budget=40
        )

        assert low.exempt_count == 2
        assert high.exempt_count == 0


class TestExemptSelection:
    """Task 3.11: bounded by count, and selecting the EARLIEST."""

    @staticmethod
    def _thread(n_success, n_refusal, tool="search_vectorstore_hybrid"):
        msgs = [HumanMessage(content="q")]
        for i in range(n_success):
            msgs.append(
                AIMessage(
                    content="", tool_calls=[{"name": tool, "args": {}, "id": f"s{i}"}]
                )
            )
            msgs.append(
                ToolMessage(content=f"EVIDENCE {i}", tool_call_id=f"s{i}", name=tool)
            )
        for i in range(n_refusal):
            msgs.append(
                AIMessage(
                    content="", tool_calls=[{"name": tool, "args": {}, "id": f"r{i}"}]
                )
            )
            msgs.append(
                ToolMessage(
                    content="Search budget exhausted", tool_call_id=f"r{i}", name=tool
                )
            )
        return msgs

    def test_selects_the_earliest_results_up_to_the_limit(self):
        msgs = self._thread(n_success=2, n_refusal=3)

        idx = select_exempt_indices(msgs, "search_vectorstore_hybrid", limit=2)

        chosen = [msgs[i].content for i in sorted(idx)]
        assert chosen == ["EVIDENCE 0", "EVIDENCE 1"]

    def test_refusals_are_not_exempt(self):
        """Selecting the NEWEST inverts this — it protects refusals."""
        msgs = self._thread(n_success=2, n_refusal=3)

        idx = select_exempt_indices(msgs, "search_vectorstore_hybrid", limit=2)

        for i in idx:
            assert "budget exhausted" not in msgs[i].content

    def test_fewer_results_than_the_limit_selects_all_of_them(self):
        msgs = self._thread(n_success=1, n_refusal=0)
        idx = select_exempt_indices(msgs, "search_vectorstore_hybrid", limit=2)
        assert len(idx) == 1

    def test_other_tools_are_never_selected(self):
        msgs = self._thread(n_success=2, n_refusal=0)
        msgs.append(
            AIMessage(content="", tool_calls=[{"name": "other", "args": {}, "id": "o"}])
        )
        msgs.append(ToolMessage(content="OTHER", tool_call_id="o", name="other"))

        idx = select_exempt_indices(msgs, "search_vectorstore_hybrid", limit=5)

        assert all(msgs[i].name == "search_vectorstore_hybrid" for i in idx)

    def test_a_zero_limit_exempts_nothing(self):
        msgs = self._thread(n_success=2, n_refusal=0)
        assert (
            select_exempt_indices(msgs, "search_vectorstore_hybrid", limit=0) == set()
        )
