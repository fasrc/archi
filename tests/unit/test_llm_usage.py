"""Unit tests for the LLM usage recorder helper (D1-D3, D6).

Covers:
  (a) basic usage_metadata → snapshot shape
  (b) response_metadata model_name and model overrides
  (c) multi-generation / multi-call accumulation
  (d) two models → two by_model entries, sorted, top-level = sums
  (e) no usage anywhere → unreported_calls
  (f) response_metadata token_usage / usage fallbacks
  (g) invalid count types → unreported
  (h) snapshot() is None before any on_llm_end
  (i) sum_usage edge cases
  (j) phase_usage_totals
  (k) no total_tokens key in snapshot
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, Generation, LLMResult

from src.utils.llm_usage import UsageRecorder, phase_usage_totals, sum_usage


def _make_result(
    msgs: List[Optional[AIMessage]],
    response_metadata: Optional[Dict[str, Any]] = None,
) -> LLMResult:
    gens = []
    for msg in msgs:
        if msg is None:
            continue
        gen = ChatGeneration(message=msg)
        if response_metadata is not None:
            gen.generation_info = response_metadata
        gens.append(gen)
    return LLMResult(generations=[gens])


def _ai(
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    response_metadata: Optional[Dict[str, Any]] = None,
) -> AIMessage:
    usage: Dict[str, Any] = {}
    if input_tokens is not None and output_tokens is not None:
        usage["input_tokens"] = input_tokens
        usage["output_tokens"] = output_tokens
        usage["total_tokens"] = input_tokens + output_tokens
    return AIMessage(
        content="hi",
        usage_metadata=usage if usage else None,
        response_metadata=response_metadata or {},
    )


# --- (a) basic snapshot shape -----------------------------------------------


def test_basic_usage_metadata_snapshot():
    rec = UsageRecorder(provider="acme", model="m1")
    msg = _ai(input_tokens=120, output_tokens=30)
    result = _make_result([msg])
    rec.on_llm_end(result)

    snap = rec.snapshot()
    assert snap is not None
    assert snap["input_tokens"] == 120
    assert snap["output_tokens"] == 30
    assert snap["calls"] == 1
    assert snap["unreported_calls"] == 0
    assert len(snap["by_model"]) == 1
    entry = snap["by_model"][0]
    assert entry["provider"] == "acme"
    assert entry["model"] == "m1"
    assert entry["input_tokens"] == 120
    assert entry["output_tokens"] == 30
    assert entry["calls"] == 1
    assert entry["unreported_calls"] == 0


# --- (k) no total_tokens key -------------------------------------------------


def test_no_total_tokens_key():
    rec = UsageRecorder(provider="p", model="m")
    rec.on_llm_end(_make_result([_ai(10, 5)]))
    snap = rec.snapshot()
    assert snap is not None
    assert "total_tokens" not in snap
    assert "total_tokens" not in snap["by_model"][0]


# --- (b) response_metadata model overrides -----------------------------------


def test_response_metadata_model_name_override():
    rec = UsageRecorder(provider="p", model="constructor-model")
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        response_metadata={"model_name": "override-model"},
    )
    rec.on_llm_end(_make_result([msg]))
    snap = rec.snapshot()
    assert snap is not None
    assert snap["by_model"][0]["model"] == "override-model"


def test_response_metadata_model_second_choice():
    rec = UsageRecorder(provider="p", model="constructor-model")
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        response_metadata={"model": "alt-model"},
    )
    rec.on_llm_end(_make_result([msg]))
    snap = rec.snapshot()
    assert snap is not None
    assert snap["by_model"][0]["model"] == "alt-model"


def test_provider_always_from_constructor():
    rec = UsageRecorder(provider="my-provider", model="m")
    msg = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        response_metadata={"model_name": "other"},
    )
    rec.on_llm_end(_make_result([msg]))
    snap = rec.snapshot()
    assert snap is not None
    assert snap["by_model"][0]["provider"] == "my-provider"


# --- (c) multi-generation / multi-call accumulation -------------------------


def test_candidates_of_one_call_count_once():
    # langchain_openai copies the request-level usage onto every candidate
    # when n > 1, so summing candidates multiplies the call's tokens.
    rec = UsageRecorder(provider="p", model="m")
    result = LLMResult(
        generations=[
            [
                ChatGeneration(message=_ai(input_tokens=10, output_tokens=5)),
                ChatGeneration(message=_ai(input_tokens=10, output_tokens=5)),
                ChatGeneration(message=_ai(input_tokens=10, output_tokens=5)),
            ]
        ]
    )
    rec.on_llm_end(result)

    snap = rec.snapshot()
    assert snap is not None
    assert snap["calls"] == 1
    assert snap["unreported_calls"] == 0
    assert snap["input_tokens"] == 10
    assert snap["output_tokens"] == 5


def test_candidates_use_first_reporting_candidate():
    rec = UsageRecorder(provider="p", model="m")
    result = LLMResult(
        generations=[
            [
                ChatGeneration(message=_ai()),
                ChatGeneration(message=_ai(input_tokens=7, output_tokens=3)),
            ]
        ]
    )
    rec.on_llm_end(result)

    snap = rec.snapshot()
    assert snap is not None
    assert snap["calls"] == 1
    assert snap["unreported_calls"] == 0
    assert snap["input_tokens"] == 7
    assert snap["output_tokens"] == 3


def test_candidates_without_usage_are_one_unreported_call():
    rec = UsageRecorder(provider="p", model="m")
    result = LLMResult(
        generations=[[ChatGeneration(message=_ai()), ChatGeneration(message=_ai())]]
    )
    rec.on_llm_end(result)

    snap = rec.snapshot()
    assert snap is not None
    assert snap["calls"] == 1
    assert snap["unreported_calls"] == 1
    assert snap["input_tokens"] == 0


def test_non_chat_generation_is_one_unreported_call():
    rec = UsageRecorder(provider="p", model="m")
    rec.on_llm_end(LLMResult(generations=[[Generation(text="hi")]]))

    snap = rec.snapshot()
    assert snap is not None
    assert snap["calls"] == 1
    assert snap["unreported_calls"] == 1
    assert snap["by_model"][0]["model"] == "m"


def test_two_prompts_in_one_llm_result_are_two_calls():
    rec = UsageRecorder(provider="p", model="m")
    result = LLMResult(
        generations=[
            [ChatGeneration(message=_ai(input_tokens=10, output_tokens=5))],
            [ChatGeneration(message=_ai(input_tokens=20, output_tokens=8))],
        ]
    )
    rec.on_llm_end(result)

    snap = rec.snapshot()
    assert snap is not None
    assert snap["calls"] == 2
    assert snap["input_tokens"] == 30
    assert snap["output_tokens"] == 13


def test_two_on_llm_end_calls_sum():
    rec = UsageRecorder(provider="p", model="m")
    rec.on_llm_end(_make_result([_ai(input_tokens=100, output_tokens=20)]))
    rec.on_llm_end(_make_result([_ai(input_tokens=50, output_tokens=10)]))

    snap = rec.snapshot()
    assert snap is not None
    assert snap["calls"] == 2
    assert snap["input_tokens"] == 150
    assert snap["output_tokens"] == 30


# --- (d) two models → two by_model entries, sorted ---------------------------


def test_two_models_two_by_model_entries_sorted():
    rec = UsageRecorder(provider="p", model="m-b")
    msg_b = _ai(input_tokens=5, output_tokens=2)
    rec.on_llm_end(_make_result([msg_b]))

    msg_a = AIMessage(
        content="y",
        usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        response_metadata={"model_name": "m-a"},
    )
    rec.on_llm_end(_make_result([msg_a]))

    snap = rec.snapshot()
    assert snap is not None
    assert len(snap["by_model"]) == 2
    # sorted by (provider, model): m-a < m-b
    assert snap["by_model"][0]["model"] == "m-a"
    assert snap["by_model"][1]["model"] == "m-b"
    # top-level = sums
    assert snap["input_tokens"] == 15
    assert snap["output_tokens"] == 5
    assert snap["calls"] == 2


# --- (e) no usage anywhere → unreported_calls --------------------------------


def test_no_usage_gives_unreported_calls():
    rec = UsageRecorder(provider="p", model="m")
    msg = AIMessage(content="hi", usage_metadata=None, response_metadata={})
    rec.on_llm_end(_make_result([msg]))

    snap = rec.snapshot()
    assert snap is not None
    assert snap["calls"] == 1
    assert snap["unreported_calls"] == 1
    assert snap["input_tokens"] == 0
    assert snap["output_tokens"] == 0


# --- (f) response_metadata fallbacks ----------------------------------------


def test_response_metadata_token_usage_prompt_completion():
    rec = UsageRecorder(provider="p", model="m")
    msg = AIMessage(
        content="hi",
        usage_metadata=None,
        response_metadata={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}},
    )
    rec.on_llm_end(_make_result([msg]))

    snap = rec.snapshot()
    assert snap is not None
    assert snap["input_tokens"] == 7
    assert snap["output_tokens"] == 3
    assert snap["unreported_calls"] == 0


def test_response_metadata_usage_input_output():
    rec = UsageRecorder(provider="p", model="m")
    msg = AIMessage(
        content="hi",
        usage_metadata=None,
        response_metadata={"usage": {"input_tokens": 9, "output_tokens": 4}},
    )
    rec.on_llm_end(_make_result([msg]))

    snap = rec.snapshot()
    assert snap is not None
    assert snap["input_tokens"] == 9
    assert snap["output_tokens"] == 4
    assert snap["unreported_calls"] == 0


# --- (g) invalid count types → unreported ------------------------------------
# AIMessage.usage_metadata is pydantic-typed; invalid types are passed through
# response_metadata["usage"] where no schema enforcement applies.


def test_bool_count_via_response_metadata_is_unreported():
    rec = UsageRecorder(provider="p", model="m")
    msg = AIMessage(
        content="hi",
        usage_metadata=None,
        response_metadata={"usage": {"input_tokens": True, "output_tokens": 5}},
    )
    rec.on_llm_end(_make_result([msg]))
    snap = rec.snapshot()
    assert snap is not None
    assert snap["unreported_calls"] == 1


def test_negative_count_via_response_metadata_is_unreported():
    rec = UsageRecorder(provider="p", model="m")
    msg = AIMessage(
        content="hi",
        usage_metadata=None,
        response_metadata={"usage": {"input_tokens": -1, "output_tokens": 5}},
    )
    rec.on_llm_end(_make_result([msg]))
    snap = rec.snapshot()
    assert snap is not None
    assert snap["unreported_calls"] == 1


def test_float_count_via_response_metadata_is_unreported():
    rec = UsageRecorder(provider="p", model="m")
    msg = AIMessage(
        content="hi",
        usage_metadata=None,
        response_metadata={"usage": {"input_tokens": 1.5, "output_tokens": 5}},
    )
    rec.on_llm_end(_make_result([msg]))
    snap = rec.snapshot()
    assert snap is not None
    assert snap["unreported_calls"] == 1


def test_string_count_via_response_metadata_is_unreported():
    rec = UsageRecorder(provider="p", model="m")
    msg = AIMessage(
        content="hi",
        usage_metadata=None,
        response_metadata={"usage": {"input_tokens": "10", "output_tokens": 5}},
    )
    rec.on_llm_end(_make_result([msg]))
    snap = rec.snapshot()
    assert snap is not None
    assert snap["unreported_calls"] == 1


# --- (h) snapshot() is None before any on_llm_end ---------------------------


def test_snapshot_is_none_before_any_call():
    rec = UsageRecorder(provider="p", model="m")
    assert rec.snapshot() is None


# --- (i) sum_usage edge cases ------------------------------------------------


def test_sum_usage_empty_list_returns_none():
    assert sum_usage([]) is None


def test_sum_usage_all_none_returns_none():
    assert sum_usage([None, None]) is None


def test_sum_usage_merges_by_model():
    u1 = {
        "input_tokens": 10,
        "output_tokens": 5,
        "calls": 1,
        "unreported_calls": 0,
        "by_model": [
            {
                "provider": "p",
                "model": "m",
                "input_tokens": 10,
                "output_tokens": 5,
                "calls": 1,
                "unreported_calls": 0,
            }
        ],
    }
    u2 = {
        "input_tokens": 20,
        "output_tokens": 8,
        "calls": 2,
        "unreported_calls": 1,
        "by_model": [
            {
                "provider": "p",
                "model": "m",
                "input_tokens": 20,
                "output_tokens": 8,
                "calls": 2,
                "unreported_calls": 1,
            }
        ],
    }
    result = sum_usage([u1, u2])
    assert result is not None
    assert result["input_tokens"] == 30
    assert result["output_tokens"] == 13
    assert result["calls"] == 3
    assert result["unreported_calls"] == 1
    assert len(result["by_model"]) == 1
    assert result["by_model"][0]["input_tokens"] == 30
    assert result["by_model"][0]["calls"] == 3


def test_sum_usage_skips_none_inputs():
    u = {
        "input_tokens": 5,
        "output_tokens": 2,
        "calls": 1,
        "unreported_calls": 0,
        "by_model": [
            {
                "provider": "p",
                "model": "m",
                "input_tokens": 5,
                "output_tokens": 2,
                "calls": 1,
                "unreported_calls": 0,
            }
        ],
    }
    result = sum_usage([None, u, None])
    assert result is not None
    assert result["input_tokens"] == 5


def test_sum_usage_non_dict_raises():
    with pytest.raises(ValueError):
        sum_usage(["not-a-dict"])  # type: ignore[list-item]


def test_sum_usage_merges_different_models():
    u1 = {
        "input_tokens": 10,
        "output_tokens": 5,
        "calls": 1,
        "unreported_calls": 0,
        "by_model": [
            {
                "provider": "p",
                "model": "a",
                "input_tokens": 10,
                "output_tokens": 5,
                "calls": 1,
                "unreported_calls": 0,
            }
        ],
    }
    u2 = {
        "input_tokens": 20,
        "output_tokens": 8,
        "calls": 1,
        "unreported_calls": 0,
        "by_model": [
            {
                "provider": "p",
                "model": "b",
                "input_tokens": 20,
                "output_tokens": 8,
                "calls": 1,
                "unreported_calls": 0,
            }
        ],
    }
    result = sum_usage([u1, u2])
    assert result is not None
    assert len(result["by_model"]) == 2
    models = [e["model"] for e in result["by_model"]]
    assert models == sorted(models)


# --- (j) phase_usage_totals --------------------------------------------------


def test_phase_usage_totals_sums_phases():
    prep_rows = [
        {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "calls": 1,
                "unreported_calls": 0,
                "by_model": [
                    {
                        "provider": "p",
                        "model": "m",
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "calls": 1,
                        "unreported_calls": 0,
                    }
                ],
            }
        },
        {"usage": None},
    ]
    answer_rows = [
        {
            "usage": {
                "input_tokens": 20,
                "output_tokens": 8,
                "calls": 1,
                "unreported_calls": 0,
                "by_model": [
                    {
                        "provider": "p",
                        "model": "m",
                        "input_tokens": 20,
                        "output_tokens": 8,
                        "calls": 1,
                        "unreported_calls": 0,
                    }
                ],
            }
        },
    ]
    result_rows = [
        {
            "usage": {
                "input_tokens": 3,
                "output_tokens": 1,
                "calls": 1,
                "unreported_calls": 0,
                "by_model": [
                    {
                        "provider": "p",
                        "model": "m",
                        "input_tokens": 3,
                        "output_tokens": 1,
                        "calls": 1,
                        "unreported_calls": 0,
                    }
                ],
            }
        },
    ]

    totals = phase_usage_totals(prep_rows, answer_rows, result_rows)
    assert totals["prepare"] is not None
    assert totals["prepare"]["input_tokens"] == 10
    assert totals["run"] is not None
    assert totals["run"]["input_tokens"] == 20
    assert totals["score"] is not None
    assert totals["score"]["input_tokens"] == 3


def test_phase_usage_totals_none_for_no_usage_rows():
    prep_rows: list = []
    answer_rows = [{"usage": None}]
    result_rows = [{"no_usage_key": True}]

    totals = phase_usage_totals(prep_rows, answer_rows, result_rows)
    assert totals["prepare"] is None
    assert totals["run"] is None
    assert totals["score"] is None
