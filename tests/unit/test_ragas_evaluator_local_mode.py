"""The RAGAS judge must honour the same local-mode resolution as the SUT (issue #73).

``Benchmarker.get_ragas_llm_evaluator`` falls back to the benchmark provider/model
when no independent ``evaluator_*`` is configured. For a ``provider: local`` SUT
pointed at an OpenAI-compatible endpoint (``.../v1``), the judge previously hard-coded
``ChatOllama`` and would 404 against the wrong client — answering questions with
ChatOpenAI and then failing to score them. These tests pin that the fallback builds
an OpenAI-compatible client for a ``/v1`` judge endpoint while native Ollama still
uses ``ChatOllama``.
"""

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
