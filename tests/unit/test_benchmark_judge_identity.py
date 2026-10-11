"""Tests for ragas_judge_effective_identity (issue #618).

Pins the effective RAGAS judge identity for each provider row in the
design.md provider table.  Each test asserts the full
{"provider", "model", "base_url", "mode"} dict.

Task 2.1 section: after get_ragas_llm_evaluator() the builder stores the
identity on self._ragas_judge_identity and every arm passes its endpoint
from that dict.
"""

import unittest.mock

from src.archi.providers.local_provider import normalize_base_url
from src.bin.service_benchmark import Benchmarker, ragas_judge_effective_identity


def _cfg(benchmarking=None, ragas_settings=None):
    """Build a minimal config with the given benchmarking keys."""
    bench = dict(benchmarking or {})
    bench.setdefault("mode_settings", {})["ragas_settings"] = ragas_settings or {}
    return {"services": {"benchmarking": bench}}


# ---------------------------------------------------------------------------
# openai
# ---------------------------------------------------------------------------


def test_openai_provider_no_base_url():
    cfg = _cfg({"provider": "openai", "model": "gpt-4o"})
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "openai",
        "model": "gpt-4o",
        "base_url": None,
        "mode": None,
    }


# ---------------------------------------------------------------------------
# ollama
# ---------------------------------------------------------------------------


def test_ollama_uses_evaluator_ollama_url_when_set():
    cfg = _cfg(
        {"provider": "ollama", "model": "llama3", "ollama_url": "http://sut:11434"},
        ragas_settings={"evaluator_ollama_url": "http://judge:11434"},
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "ollama",
        "model": "llama3",
        "base_url": "http://judge:11434",
        "mode": None,
    }


def test_ollama_falls_back_to_ollama_url():
    cfg = _cfg(
        {
            "provider": "ollama",
            "model": "llama3",
            "ollama_url": "http://localhost:11434",
        }
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "ollama",
        "model": "llama3",
        "base_url": "http://localhost:11434",
        "mode": None,
    }


# ---------------------------------------------------------------------------
# local — mode resolution
# ---------------------------------------------------------------------------


def test_local_v1_url_resolves_openai_compat():
    cfg = _cfg(
        {
            "provider": "local",
            "model": "qwen-x",
            "ollama_url": "http://archi.rc.fas.harvard.edu:8001/v1",
        }
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "local",
        "model": "qwen-x",
        "base_url": "http://archi.rc.fas.harvard.edu:8001/v1",
        "mode": "openai_compat",
    }


def test_local_non_v1_url_resolves_ollama():
    cfg = _cfg(
        {"provider": "local", "model": "qwen-x", "ollama_url": "http://localhost:11434"}
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "local",
        "model": "qwen-x",
        "base_url": "http://localhost:11434",
        "mode": "ollama",
    }


def test_local_empty_evaluator_mode_inherits_sut_provider_mode():
    """evaluator_provider_mode: "" (not configured) → inherit the SUT provider_mode."""
    cfg = _cfg(
        {
            "provider": "local",
            "model": "qwen-x",
            "ollama_url": "http://vllm:8001",
            "provider_mode": "openai_compat",
        },
        ragas_settings={"evaluator_provider_mode": ""},
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "local",
        "model": "qwen-x",
        "base_url": "http://vllm:8001",
        "mode": "openai_compat",
    }


# ---------------------------------------------------------------------------
# huggingface
# ---------------------------------------------------------------------------


def test_huggingface_with_evaluator_url():
    cfg = _cfg(
        {"model": "sut-model"},
        ragas_settings={
            "evaluator_provider": "huggingface",
            "evaluator_model": "judge-x",
            "evaluator_ollama_url": "http://judge-host:8001/v1",
        },
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "huggingface",
        "model": "judge-x",
        "base_url": "http://judge-host:8001/v1",
        "mode": None,
    }


def test_huggingface_without_url_defaults_to_local_port():
    cfg = _cfg(
        {"model": "sut-model"},
        ragas_settings={
            "evaluator_provider": "huggingface",
            "evaluator_model": "judge-x",
        },
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "huggingface",
        "model": "judge-x",
        "base_url": "http://localhost:8000/v1",
        "mode": None,
    }


# ---------------------------------------------------------------------------
# anthropic
# ---------------------------------------------------------------------------


def test_anthropic_provider_no_base_url():
    cfg = _cfg(
        {"model": "sut-model"},
        ragas_settings={
            "evaluator_provider": "anthropic",
            "evaluator_model": "claude-3-5-sonnet-20241022",
        },
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "anthropic",
        "model": "claude-3-5-sonnet-20241022",
        "base_url": None,
        "mode": None,
    }


# ---------------------------------------------------------------------------
# huit_bedrock
# ---------------------------------------------------------------------------


def test_huit_bedrock_with_benchmarking_base_url():
    cfg = _cfg(
        {
            "model": "sut-model",
            "base_url": "https://custom.huit.edu/v2",
        },
        ragas_settings={
            "evaluator_provider": "huit_bedrock",
            "evaluator_model": "anthropic.claude-x",
        },
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "huit_bedrock",
        "model": "anthropic.claude-x",
        "base_url": "https://custom.huit.edu/v2",
        "mode": None,
    }


def test_huit_bedrock_without_base_url_uses_default():
    cfg = _cfg(
        {"model": "sut-model"},
        ragas_settings={
            "evaluator_provider": "huit_bedrock",
            "evaluator_model": "anthropic.claude-x",
        },
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "huit_bedrock",
        "model": "anthropic.claude-x",
        "base_url": "https://go.apis.huit.harvard.edu/ais-bedrock-llm/v2",
        "mode": None,
    }


# ---------------------------------------------------------------------------
# unknown provider (falls through to ChatOpenAI with no endpoint)
# ---------------------------------------------------------------------------


def test_unknown_provider_no_base_url():
    cfg = _cfg(
        {"model": "sut-model"},
        ragas_settings={
            "evaluator_provider": "my_custom_llm",
            "evaluator_model": "some-model",
        },
    )
    assert ragas_judge_effective_identity(cfg) == {
        "provider": "my_custom_llm",
        "model": "some-model",
        "base_url": None,
        "mode": None,
    }


# ---------------------------------------------------------------------------
# Task 2.1 — builder stores identity and passes its endpoint to clients
# ---------------------------------------------------------------------------


def _make_bench(benchmarking, ragas_settings=None):
    """Benchmarker stub that bypasses __init__; only self.config is set."""
    bench = object.__new__(Benchmarker)
    b = dict(benchmarking)
    b.setdefault("mode_settings", {})["ragas_settings"] = ragas_settings or {}
    bench.config = {"services": {"benchmarking": b}}
    return bench


def test_evaluator_stores_effective_identity():
    bench = _make_bench({"provider": "openai", "model": "gpt-4o"})
    with unittest.mock.patch("src.bin.service_benchmark.ChatOpenAI"):
        bench.get_ragas_llm_evaluator()
    assert bench._ragas_judge_identity == ragas_judge_effective_identity(bench.config)


def test_local_openai_compat_client_choice_agrees_with_identity_mode():
    bench = _make_bench(
        {"provider": "local", "model": "qwen-x", "ollama_url": "http://host:8001/v1"}
    )
    with (
        unittest.mock.patch("src.bin.service_benchmark.get_model") as mock_gm,
        unittest.mock.patch("langchain_ollama.ChatOllama") as mock_co,
    ):
        bench.get_ragas_llm_evaluator()
    assert mock_gm.called
    assert not mock_co.called
    assert bench._ragas_judge_identity["mode"] == "openai_compat"


def test_local_ollama_client_choice_agrees_with_identity_mode():
    bench = _make_bench(
        {"provider": "local", "model": "qwen-x", "ollama_url": "http://host:11434"}
    )
    with (
        unittest.mock.patch("src.bin.service_benchmark.get_model") as mock_gm,
        unittest.mock.patch("langchain_ollama.ChatOllama") as mock_co,
    ):
        bench.get_ragas_llm_evaluator()
    assert mock_co.called
    assert not mock_gm.called
    assert bench._ragas_judge_identity["mode"] == "ollama"


def test_ollama_evaluator_url_endpoint_agrees_with_identity():
    bench = _make_bench(
        {"provider": "ollama", "model": "llama3", "ollama_url": "http://sut:11434"},
        ragas_settings={"evaluator_ollama_url": "http://judge:11434"},
    )
    with unittest.mock.patch("langchain_ollama.ChatOllama") as mock_co:
        bench.get_ragas_llm_evaluator()
    identity = bench._ragas_judge_identity
    assert mock_co.call_args.kwargs["base_url"] == identity["base_url"]


def test_ollama_fallback_url_endpoint_agrees_with_identity():
    bench = _make_bench(
        {
            "provider": "ollama",
            "model": "llama3",
            "ollama_url": "http://localhost:11434",
        }
    )
    with unittest.mock.patch("langchain_ollama.ChatOllama") as mock_co:
        bench.get_ragas_llm_evaluator()
    identity = bench._ragas_judge_identity
    assert mock_co.call_args.kwargs["base_url"] == identity["base_url"]


def test_local_openai_compat_endpoint_agrees_with_identity():
    bench = _make_bench(
        {
            "provider": "local",
            "model": "qwen-x",
            "ollama_url": "http://archi.rc.fas.harvard.edu:8001/v1",
        }
    )
    with unittest.mock.patch("src.bin.service_benchmark.get_model") as mock_gm:
        bench.get_ragas_llm_evaluator()
    identity = bench._ragas_judge_identity
    assert mock_gm.call_args.kwargs["base_url"] == normalize_base_url(
        identity["base_url"]
    )


def test_local_ollama_endpoint_agrees_with_identity():
    bench = _make_bench(
        {"provider": "local", "model": "qwen-x", "ollama_url": "http://localhost:11434"}
    )
    with unittest.mock.patch("langchain_ollama.ChatOllama") as mock_co:
        bench.get_ragas_llm_evaluator()
    identity = bench._ragas_judge_identity
    assert mock_co.call_args.kwargs["base_url"] == identity["base_url"]


def test_huggingface_with_url_endpoint_agrees_with_identity():
    bench = _make_bench(
        {"model": "sut-model"},
        ragas_settings={
            "evaluator_provider": "huggingface",
            "evaluator_model": "judge-x",
            "evaluator_ollama_url": "http://judge-host:8001/v1",
        },
    )
    with unittest.mock.patch("src.bin.service_benchmark.get_model") as mock_gm:
        bench.get_ragas_llm_evaluator()
    identity = bench._ragas_judge_identity
    assert mock_gm.call_args.kwargs["base_url"] == normalize_base_url(
        identity["base_url"]
    )


def test_huggingface_without_url_endpoint_agrees_with_identity():
    bench = _make_bench(
        {"model": "sut-model"},
        ragas_settings={
            "evaluator_provider": "huggingface",
            "evaluator_model": "judge-x",
        },
    )
    with unittest.mock.patch("src.bin.service_benchmark.get_model") as mock_gm:
        bench.get_ragas_llm_evaluator()
    identity = bench._ragas_judge_identity
    assert mock_gm.call_args.kwargs["base_url"] == normalize_base_url(
        identity["base_url"]
    )


def test_huit_bedrock_with_base_url_endpoint_agrees_with_identity():
    bench = _make_bench(
        {"model": "sut-model", "base_url": "https://custom.huit.edu/v2"},
        ragas_settings={
            "evaluator_provider": "huit_bedrock",
            "evaluator_model": "anthropic.claude-x",
        },
    )
    with unittest.mock.patch("src.bin.service_benchmark.get_model") as mock_gm:
        bench.get_ragas_llm_evaluator()
    identity = bench._ragas_judge_identity
    assert mock_gm.call_args.args[2]["base_url"] == identity["base_url"]


def test_huit_bedrock_without_base_url_endpoint_agrees_with_identity():
    bench = _make_bench(
        {"model": "sut-model"},
        ragas_settings={
            "evaluator_provider": "huit_bedrock",
            "evaluator_model": "anthropic.claude-x",
        },
    )
    with unittest.mock.patch("src.bin.service_benchmark.get_model") as mock_gm:
        bench.get_ragas_llm_evaluator()
    identity = bench._ragas_judge_identity
    assert mock_gm.call_args.args[2]["base_url"] == identity["base_url"]
