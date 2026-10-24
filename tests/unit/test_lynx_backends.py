"""The Lynx backends: a recording stub and a vLLM offline backend (#734, D1, D2)."""

import sys
import types

import pytest

from scripts.benchmarking.lynx import backends


def test_the_stub_records_what_it_got_and_answers_pass_by_default():
    stub = backends.StubBackend()
    outputs = stub.generate(["first prompt", "second prompt"])
    assert stub.received == ["first prompt", "second prompt"]
    assert len(outputs) == 2
    assert all('"SCORE": "PASS"' in text for text in outputs)


def test_the_stub_takes_a_function_of_the_prompt():
    stub = backends.StubBackend(output=lambda prompt: prompt.upper())
    assert stub.generate(["a", "b"]) == ["A", "B"]


def test_the_stub_counts_words_unless_given_a_counter():
    assert backends.StubBackend().count_tokens("one two  three\nfour") == 4
    assert backends.StubBackend(token_counter=len).count_tokens("abc") == 3


class _FakeTokenizer:
    def __init__(self):
        self.templated = []

    def apply_chat_template(self, messages, tokenize, add_generation_prompt):
        assert tokenize is False and add_generation_prompt is True
        assert [m["role"] for m in messages] == ["user"]
        text = f"<user> {messages[0]['content']} <assistant>"
        self.templated.append(text)
        return text

    def encode(self, text, add_special_tokens):
        assert add_special_tokens is False
        return [hash(word) % 1000 for word in text.split()]


def _fake_vllm(calls):
    module = types.ModuleType("vllm")
    tokenizer = _FakeTokenizer()

    class SamplingParams:
        def __init__(self, **kwargs):
            calls["sampling"] = kwargs

    class LLM:
        def __init__(self, **kwargs):
            calls["llm"] = kwargs

        def get_tokenizer(self):
            return tokenizer

        def generate(self, requests, params):
            calls["requests"] = requests
            calls["params"] = params
            return [
                types.SimpleNamespace(
                    outputs=[
                        types.SimpleNamespace(text=f"out {len(r['prompt_token_ids'])}")
                    ]
                )
                for r in requests
            ]

    module.LLM = LLM
    module.SamplingParams = SamplingParams
    return module, tokenizer


def test_the_vllm_backend_loads_the_pinned_revision(monkeypatch):
    calls = {}
    module, _ = _fake_vllm(calls)
    monkeypatch.setitem(sys.modules, "vllm", module)
    backends.VllmBackend("PatronusAI/Lynx", "abc123")
    assert calls["llm"] == {
        "model": "PatronusAI/Lynx",
        "revision": "abc123",
        "tokenizer_revision": "abc123",
    }
    assert calls["sampling"] == {"temperature": 0, "max_tokens": 600}


def test_the_vllm_backend_sends_one_user_chat_message_per_prompt(monkeypatch):
    calls = {}
    module, tokenizer = _fake_vllm(calls)
    monkeypatch.setitem(sys.modules, "vllm", module)
    backend = backends.VllmBackend("m", "r")
    outputs = backend.generate(["a b c", "d"])
    # "<user> a b c <assistant>" is 5 words, "<user> d <assistant>" is 3.
    assert outputs == ["out 5", "out 3"]
    assert [len(r["prompt_token_ids"]) for r in calls["requests"]] == [5, 3]
    assert tokenizer.templated[-2:] == [
        "<user> a b c <assistant>",
        "<user> d <assistant>",
    ]


def test_the_vllm_backend_counts_the_chat_text(monkeypatch):
    calls = {}
    module, _ = _fake_vllm(calls)
    monkeypatch.setitem(sys.modules, "vllm", module)
    backend = backends.VllmBackend("m", "r")
    assert backend.count_tokens("a b c") == 5


def test_the_vllm_backend_needs_vllm(monkeypatch):
    monkeypatch.setitem(sys.modules, "vllm", None)
    with pytest.raises(ImportError):
        backends.VllmBackend("m", "r")


def test_make_backend_builds_each_kind(monkeypatch):
    calls = {}
    module, _ = _fake_vllm(calls)
    monkeypatch.setitem(sys.modules, "vllm", module)
    assert isinstance(backends.make_backend("stub", "m", "r"), backends.StubBackend)
    assert isinstance(backends.make_backend("vllm", "m", "r"), backends.VllmBackend)
    with pytest.raises(ValueError, match="unknown backend 'tgi'"):
        backends.make_backend("tgi", "m", "r")
