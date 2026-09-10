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


def test_local_openai_compat_judge_without_a_url_keeps_the_provider_default(
    monkeypatch,
):
    """A missing judge URL must not blank out the endpoint.

    Round 1 passed `base_url` as a keyword so it would outrank OLLAMA_HOST. An
    unset URL made that keyword `None`, which lands last in ChatOpenAI's kwargs
    and erases LocalProvider's own local default — sending judge prompts to the
    public OpenAI endpoint. The keyword is an override, so it is only supplied
    when there is something to override with.
    """
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    bench = _bench(
        {
            "provider": "openai",
            "model": "gpt-x",
            "mode_settings": {
                "ragas_settings": {
                    "evaluator_provider": "local",
                    "evaluator_model": "judge-x",
                    "evaluator_provider_mode": "openai_compat",
                }
            },
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert llm.openai_api_base == "http://localhost:11434"


def test_local_openai_compat_judge_without_a_url_still_inherits_ollama_host(
    monkeypatch,
):
    """With no judge URL configured, the exported host is the only signal left."""
    monkeypatch.setenv("OLLAMA_HOST", "http://sut-host:9000/v1")
    bench = _bench(
        {
            "provider": "openai",
            "model": "gpt-x",
            "mode_settings": {
                "ragas_settings": {
                    "evaluator_provider": "local",
                    "evaluator_model": "judge-x",
                    "evaluator_provider_mode": "openai_compat",
                }
            },
        }
    )
    llm = bench.get_ragas_llm_evaluator()
    assert llm.openai_api_base == "http://sut-host:9000/v1"


def test_judge_url_without_a_scheme_is_normalized(monkeypatch):
    """The keyword override must not skip the normalization the provider applies.

    `LocalProvider` prefixes a scheme-less base URL with `http://`. Passing the raw
    value as a keyword lands it after that step, so `ChatOpenAI` receives an address
    its transport cannot use and the first judge request fails.
    """
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    for provider in ("local", "huggingface"):
        bench = _bench(
            {
                "provider": "openai",
                "model": "gpt-x",
                "mode_settings": {
                    "ragas_settings": {
                        "evaluator_provider": provider,
                        "evaluator_model": "judge-x",
                        "evaluator_ollama_url": "judge-host:8001/v1",
                    }
                },
            }
        )
        llm = bench.get_ragas_llm_evaluator()
        assert llm.openai_api_base == "http://judge-host:8001/v1", provider


def test_huggingface_judge_answers_over_a_real_socket(monkeypatch):
    """The judge must complete a request against a server, not just be constructed.

    Every other test here asserts client type and URL in process. None of them prove
    the OpenAI dialect, the auth header, the request payload, or the response parsing
    survive a real round trip -- and those only run when RAGAS invokes the judge. This
    binds an OpenAI-compatible server on an ephemeral loopback port, points the
    `huggingface` arm at it, and asserts the reply parses. `OLLAMA_HOST` names a dead
    port throughout, so a judge that inherits the system-under-test URL cannot pass.
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            received.append(
                {
                    "path": self.path,
                    "authorized": "Authorization" in self.headers,
                    "model": body.get("model"),
                }
            )
            # The provider builds a streaming client, so answer in the SSE dialect.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": body.get("model"),
                "choices": [
                    {"index": 0, "delta": {"content": "4"}, "finish_reason": None}
                ],
            }
            stop = {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": body.get("model"),
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(delta)}\n\n".encode())
            self.wfile.write(f"data: {json.dumps(stop)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        judge_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        bench = _bench(
            {
                "mode_settings": {
                    "ragas_settings": {
                        "evaluator_provider": "huggingface",
                        "evaluator_model": "judge-x",
                        "evaluator_ollama_url": judge_url,
                    }
                }
            }
        )
        llm = bench.get_ragas_llm_evaluator()
        assert llm.openai_api_base == judge_url

        reply = llm.invoke("What is 2 + 2? Answer with the number only.")
        assert reply.content == "4"
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert received == [
        {"path": "/v1/chat/completions", "authorized": True, "model": "judge-x"}
    ]
