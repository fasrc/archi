"""Branch tests for src/utils/ragas_scoring — design D7.

One test per branch of build_ragas_judge_llm (openai, ollama, local-openai-compat,
local-ollama, huggingface, anthropic, huit_bedrock, default) and per branch of
build_ragas_embeddings (openai, huggingface, default), each using stub constructors.
"""

from __future__ import annotations

import unittest.mock as mock

from src.utils.ragas_scoring import build_ragas_embeddings, build_ragas_judge_llm

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
