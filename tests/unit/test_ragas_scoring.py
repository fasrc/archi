"""Branch tests for src/utils/ragas_scoring — design D7.

One test per branch of build_ragas_judge_llm (openai, ollama, local-openai-compat,
local-ollama, huggingface, anthropic, huit_bedrock, default) and per branch of
build_ragas_embeddings (openai, huggingface, default), each using stub constructors.
Two socket tests for build_uds_judge_llm (design D6).
"""

from __future__ import annotations

import contextlib
import unittest.mock as mock

import pytest

from src.utils.ragas_scoring import (
    build_ragas_embeddings,
    build_ragas_judge_llm,
    build_uds_judge_llm,
)

# ── build_ragas_judge_llm branches ────────────────────────────────────────────


def test_build_ragas_judge_llm_openai_branch():
    stub_chat_openai = mock.MagicMock(return_value=object())
    stub_get_model = mock.MagicMock()
    identity = {"provider": "openai", "model": "gpt-4o", "base_url": None, "mode": None}
    result = build_ragas_judge_llm(
        identity, chat_openai=stub_chat_openai, get_model=stub_get_model
    )
    stub_chat_openai.assert_called_once_with(model="gpt-4o")
    assert not stub_get_model.called


def test_build_ragas_judge_llm_ollama_branch():
    stub_chat_openai = mock.MagicMock()
    stub_get_model = mock.MagicMock()
    identity = {
        "provider": "ollama",
        "model": "llama3",
        "base_url": "http://judge:11434",
        "mode": None,
    }
    with mock.patch("langchain_ollama.ChatOllama") as mock_ollama:
        build_ragas_judge_llm(
            identity, chat_openai=stub_chat_openai, get_model=stub_get_model
        )
    mock_ollama.assert_called_once_with(
        model="llama3",
        base_url="http://judge:11434",
        num_predict=-2,
        model_kwargs={"format": "json"},
    )
    assert not stub_chat_openai.called
    assert not stub_get_model.called


def test_build_ragas_judge_llm_local_openai_compat_branch():
    stub_chat_openai = mock.MagicMock()
    stub_get_model = mock.MagicMock(return_value=object())
    identity = {
        "provider": "local",
        "model": "qwen-x",
        "base_url": "http://host:8001/v1",
        "mode": "openai_compat",
    }
    build_ragas_judge_llm(
        identity, chat_openai=stub_chat_openai, get_model=stub_get_model
    )
    stub_get_model.assert_called_once()
    call_args = stub_get_model.call_args
    assert call_args.args[0] == "local"
    assert call_args.args[1] == "qwen-x"
    assert call_args.args[2]["mode"] == "openai_compat"
    assert not stub_chat_openai.called


def test_build_ragas_judge_llm_local_ollama_branch():
    stub_chat_openai = mock.MagicMock()
    stub_get_model = mock.MagicMock()
    identity = {
        "provider": "local",
        "model": "qwen-x",
        "base_url": "http://host:11434",
        "mode": "ollama",
    }
    with mock.patch("langchain_ollama.ChatOllama") as mock_ollama:
        build_ragas_judge_llm(
            identity, chat_openai=stub_chat_openai, get_model=stub_get_model
        )
    mock_ollama.assert_called_once_with(
        model="qwen-x",
        base_url="http://host:11434",
        num_predict=-2,
        model_kwargs={"format": "json"},
    )
    assert not stub_get_model.called
    assert not stub_chat_openai.called


def test_build_ragas_judge_llm_huggingface_branch():
    stub_chat_openai = mock.MagicMock()
    stub_get_model = mock.MagicMock(return_value=object())
    identity = {
        "provider": "huggingface",
        "model": "llama3",
        "base_url": "http://host:8001/v1",
        "mode": None,
    }
    build_ragas_judge_llm(
        identity, chat_openai=stub_chat_openai, get_model=stub_get_model
    )
    stub_get_model.assert_called_once()
    assert stub_get_model.call_args.args[0] == "local"
    assert not stub_chat_openai.called


def test_build_ragas_judge_llm_anthropic_branch():
    stub_chat_openai = mock.MagicMock()
    stub_get_model = mock.MagicMock()
    identity = {
        "provider": "anthropic",
        "model": "claude-3-5-sonnet-20241022",
        "base_url": None,
        "mode": None,
    }
    with mock.patch("langchain_anthropic.ChatAnthropic") as mock_anthropic:
        build_ragas_judge_llm(
            identity, chat_openai=stub_chat_openai, get_model=stub_get_model
        )
    mock_anthropic.assert_called_once_with(model="claude-3-5-sonnet-20241022")
    assert not stub_chat_openai.called
    assert not stub_get_model.called


def test_build_ragas_judge_llm_huit_bedrock_branch():
    stub_chat_openai = mock.MagicMock()
    stub_get_model = mock.MagicMock(return_value=object())
    identity = {
        "provider": "huit_bedrock",
        "model": "us.anthropic.claude-sonnet-4-5",
        "base_url": "https://go.apis.huit.harvard.edu/ais-bedrock-llm/v2",
        "mode": None,
    }
    build_ragas_judge_llm(
        identity, chat_openai=stub_chat_openai, get_model=stub_get_model
    )
    stub_get_model.assert_called_once()
    assert stub_get_model.call_args.args[0] == "huit_bedrock"
    assert not stub_chat_openai.called


def test_build_ragas_judge_llm_default_branch():
    stub_chat_openai = mock.MagicMock(return_value=object())
    stub_get_model = mock.MagicMock()
    identity = {
        "provider": "unknown_provider",
        "model": "some-model",
        "base_url": None,
        "mode": None,
    }
    build_ragas_judge_llm(
        identity, chat_openai=stub_chat_openai, get_model=stub_get_model
    )
    stub_chat_openai.assert_called_once_with(model="some-model")
    assert not stub_get_model.called


# ── build_ragas_embeddings branches ───────────────────────────────────────────


def test_build_ragas_embeddings_openai_branch():
    stub_openai = mock.MagicMock(return_value=object())
    stub_hf = mock.MagicMock()
    build_ragas_embeddings(
        "openai", openai_embeddings=stub_openai, huggingface_embeddings=stub_hf
    )
    stub_openai.assert_called_once_with()
    assert not stub_hf.called


def test_build_ragas_embeddings_huggingface_branch():
    stub_openai = mock.MagicMock()
    stub_hf = mock.MagicMock(return_value=object())
    build_ragas_embeddings(
        "huggingface", openai_embeddings=stub_openai, huggingface_embeddings=stub_hf
    )
    stub_hf.assert_called_once_with()
    assert not stub_openai.called


def test_build_ragas_embeddings_default_branch():
    stub_openai = mock.MagicMock(return_value=object())
    stub_hf = mock.MagicMock()
    build_ragas_embeddings(
        "UNKNOWN_TYPE", openai_embeddings=stub_openai, huggingface_embeddings=stub_hf
    )
    stub_openai.assert_called_once_with()
    assert not stub_hf.called


# ── build_uds_judge_llm socket tests (design D6) ─────────────────────────────


def test_build_uds_judge_llm_key_present_sends_auth_via_socket():
    import http.server
    import os
    import socketserver
    import tempfile
    import threading

    import openai

    tmpdir = tempfile.mkdtemp(prefix="rj")
    sock_path = os.path.join(tmpdir, "vllm.sock")
    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            captured["request_line"] = self.requestline
            captured["auth"] = self.headers.get("Authorization", "")
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            body = (
                b'{"id":"chatcmpl-1","object":"chat.completion","created":1000000000,'
                b'"model":"llama","choices":[{"index":0,"message":{"role":"assistant",'
                b'"content":"ok"},"finish_reason":"stop","logprobs":null}],'
                b'"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def address_string(self):
            return "unix"

        def log_message(self, *args):
            pass

    class UnixHTTPServer(socketserver.UnixStreamServer):
        def get_request(self):
            req, addr = super().get_request()
            return req, ["unix", 0]

    server = UnixHTTPServer(sock_path, Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:

        def stub_chat_openai(**kwargs):
            return openai.OpenAI(
                api_key=kwargs["api_key"],
                base_url=kwargs["base_url"],
                http_client=kwargs["http_client"],
                max_retries=kwargs.get("max_retries", 0),
            )

        with mock.patch.dict(os.environ, {"VLLM_API_KEY": "k-test"}):
            llm = build_uds_judge_llm(
                "llama",
                sock_path,
                chat_openai=stub_chat_openai,
                timeout=10,
                max_retries=0,
            )
            llm.chat.completions.create(
                model="llama",
                messages=[{"role": "user", "content": "ping"}],
            )
    finally:
        server.shutdown()
        t.join(timeout=5)

    assert captured.get("auth") == "Bearer k-test", repr(captured.get("auth"))
    assert "POST" in captured.get("request_line", ""), repr(
        captured.get("request_line")
    )
    assert "/v1/chat/completions" in captured.get("request_line", ""), repr(
        captured.get("request_line")
    )


def test_build_uds_judge_llm_key_unset_raises_value_error():
    import os
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="rj")
    sock_path = os.path.join(tmpdir, "vllm.sock")
    stub_chat_openai = mock.MagicMock()

    env_without_key = {k: v for k, v in os.environ.items() if k != "VLLM_API_KEY"}
    with mock.patch.dict(os.environ, env_without_key, clear=True):
        with pytest.raises(ValueError, match="VLLM_API_KEY"):
            build_uds_judge_llm(
                "llama",
                sock_path,
                chat_openai=stub_chat_openai,
                timeout=10,
                max_retries=0,
            )

    stub_chat_openai.assert_not_called()


@contextlib.contextmanager
def _unix_chat_server():
    """Serve a canned chat.completion on a Unix socket; yield (path, requests seen)."""
    import http.server
    import os
    import shutil
    import socketserver
    import tempfile
    import threading

    tmpdir = tempfile.mkdtemp(prefix="rj")
    sock_path = os.path.join(tmpdir, "vllm.sock")
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "auth": self.headers.get("Authorization", ""),
                }
            )
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = (
                b'{"id":"chatcmpl-1","object":"chat.completion","created":1000000000,'
                b'"model":"llama","choices":[{"index":0,"message":{"role":"assistant",'
                b'"content":"ok"},"finish_reason":"stop","logprobs":null}],'
                b'"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def address_string(self):
            return "unix"

        def log_message(self, *args):
            pass

    class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def get_request(self):
            req, _ = super().get_request()
            return req, ["unix", 0]

    server = UnixHTTPServer(sock_path, Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield sock_path, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_build_uds_judge_llm_takes_model_then_socket_and_builds_a_chat_openai(
    monkeypatch,
):
    """D6 signature: build_uds_judge_llm(model, socket_path) with ChatOpenAI as default."""
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("VLLM_API_KEY", "k-test")
    with _unix_chat_server() as (sock_path, requests):
        llm = build_uds_judge_llm("llama", sock_path, max_retries=0)
        reply = llm.invoke("ping")

    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "llama"
    assert reply.content == "ok"
    assert requests == [
        {"method": "POST", "path": "/v1/chat/completions", "auth": "Bearer k-test"}
    ]
