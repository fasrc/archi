"""The RAGAS judge must honour the same local-mode resolution as the SUT (issue #73).

``Benchmarker.get_ragas_llm_evaluator`` falls back to the benchmark provider/model
when no independent ``evaluator_*`` is configured. For a ``provider: local`` SUT
pointed at an OpenAI-compatible endpoint (``.../v1``), the judge previously hard-coded
``ChatOllama`` and would 404 against the wrong client — answering questions with
ChatOpenAI and then failing to score them. These tests pin that the fallback builds
an OpenAI-compatible client for a ``/v1`` judge endpoint while native Ollama still
uses ``ChatOllama``.
"""

import pytest
from langchain_openai import ChatOpenAI

from src.bin.service_benchmark import Benchmarker


def _bench(benchmarking):
    # Bypass __init__ (which needs a real config file on disk); the evaluator only
    # reads self.config.
    bench = object.__new__(Benchmarker)
    bench.config = {
        "services": {
            "benchmarking": {"mode_settings": {"ragas_settings": {}}, **benchmarking}
        }
    }
    return bench


def test_local_v1_judge_fallback_uses_openai_compatible_client():
    bench = _bench(
        {
            "provider": "local",
            "model": "qwen-x",
            "ollama_url": "http://archi.rc.fas.harvard.edu:8001/v1",
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert isinstance(llm, ChatOpenAI)


def test_local_native_ollama_judge_fallback_uses_chatollama():
    bench = _bench(
        {
            "provider": "local",
            "model": "qwen-x",
            "ollama_url": "http://localhost:11434",
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert type(llm).__name__ == "ChatOllama"


def test_local_explicit_provider_mode_forces_openai_compatible():
    bench = _bench(
        {
            "provider": "local",
            "model": "qwen-x",
            # base URL does not end in /v1, so only the explicit override flips it.
            "ollama_url": "http://vllm-host:8001",
            "provider_mode": "openai_compat",
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert isinstance(llm, ChatOpenAI)


# --- issue #449: the huggingface judge arm must build a client ---


def test_huggingface_judge_uses_the_configured_evaluator_url():
    bench = _bench(
        {
            "model": "qwen-x",
            "mode_settings": {
                "ragas_settings": {
                    "evaluator_provider": "huggingface",
                    "evaluator_model": "judge-x",
                    "evaluator_ollama_url": "http://judge-host:8001/v1",
                }
            },
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert isinstance(llm, ChatOpenAI)
    assert llm.openai_api_base == "http://judge-host:8001/v1"
    assert llm.model_name == "judge-x"


def test_huggingface_judge_defaults_to_the_local_openai_compatible_port():
    """Over-reach guard — passes once 1.1 has landed; not a new defect test."""
    bench = _bench(
        {
            "model": "qwen-x",
            "mode_settings": {
                "ragas_settings": {
                    "evaluator_provider": "huggingface",
                    "evaluator_model": "judge-x",
                }
            },
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert isinstance(llm, ChatOpenAI)
    assert llm.openai_api_base == "http://localhost:8000/v1"
    assert llm.model_name == "judge-x"


def test_huggingface_judge_inherits_the_sut_url_when_no_judge_url_is_set():
    """One endpoint serving both roles is configured once, on the SUT key.

    The system under test stays on a provider that exists — `huggingface` is an
    evaluator-only name — so this is a configuration an operator can actually run.
    """
    bench = _bench(
        {
            "provider": "local",
            "model": "qwen-x",
            "ollama_url": "http://sut-host:9000/v1",
            "mode_settings": {"ragas_settings": {"evaluator_provider": "huggingface"}},
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert isinstance(llm, ChatOpenAI)
    assert llm.openai_api_base == "http://sut-host:9000/v1"
    assert llm.model_name == "qwen-x"


# --- the judge endpoint must survive OLLAMA_HOST (review round 1) ---


def test_huggingface_judge_url_survives_the_sut_ollama_host(monkeypatch):
    """load_new_configuration exports the SUT url as OLLAMA_HOST before the judge is built.

    LocalProvider then replaces even an explicitly supplied base_url with that
    variable, so without the kwarg the judge scores its own answers against the
    system under test.
    """
    monkeypatch.setenv("OLLAMA_HOST", "http://sut-host:9000/v1")
    bench = _bench(
        {
            "provider": "local",
            "model": "qwen-x",
            "ollama_url": "http://sut-host:9000/v1",
            "mode_settings": {
                "ragas_settings": {
                    "evaluator_provider": "huggingface",
                    "evaluator_model": "judge-x",
                    "evaluator_ollama_url": "http://judge-host:8001/v1",
                }
            },
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert llm.openai_api_base == "http://judge-host:8001/v1"


def test_local_judge_url_survives_the_sut_ollama_host(monkeypatch):
    """The local arm reaches the same provider seam and had the same hijack."""
    monkeypatch.setenv("OLLAMA_HOST", "http://sut-host:9000/v1")
    bench = _bench(
        {
            "provider": "local",
            "model": "qwen-x",
            "ollama_url": "http://sut-host:9000/v1",
            "mode_settings": {
                "ragas_settings": {
                    "evaluator_provider": "local",
                    "evaluator_model": "judge-x",
                    "evaluator_ollama_url": "http://judge-host:8001/v1",
                }
            },
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert llm.openai_api_base == "http://judge-host:8001/v1"


def test_huggingface_judge_default_url_survives_the_sut_ollama_host(monkeypatch):
    """The fallback endpoint is the judge's own default, not the SUT's."""
    monkeypatch.setenv("OLLAMA_HOST", "http://sut-host:9000/v1")
    bench = _bench(
        {
            "model": "qwen-x",
            "mode_settings": {
                "ragas_settings": {
                    "evaluator_provider": "huggingface",
                    "evaluator_model": "judge-x",
                }
            },
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert llm.openai_api_base == "http://localhost:8000/v1"


# --- huggingface is an evaluator-only provider name (review round 1) ---


def test_huggingface_is_not_a_system_under_test_provider():
    """A `huggingface` SUT provider is rejected before any judge is built.

    `ProviderType` has no such member, and `load_new_configuration` constructs the
    SUT through `archi(...)` first, so `services.benchmarking.provider: huggingface`
    raises at pipeline construction. The judge arm is reachable only through
    `evaluator_provider`, and the spec says so.
    """
    from src.archi.providers import get_model
    from src.archi.providers.base import ProviderType

    assert "huggingface" not in {member.value for member in ProviderType}
    with pytest.raises(ValueError, match="Invalid provider type 'huggingface'"):
        get_model("huggingface", "qwen-x", {})
