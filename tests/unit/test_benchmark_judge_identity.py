"""Tests for ragas_judge_effective_identity (issue #618).

Pins the effective RAGAS judge identity for each provider row in the
design.md provider table.  Each test asserts the full
{"provider", "model", "base_url", "mode"} dict.
"""

from src.bin.service_benchmark import ragas_judge_effective_identity


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
