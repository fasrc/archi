"""Unit tests for ``RemoteEmbeddings`` (OpenAI-compatible HTTP embedding endpoint).

The class must send plain text (no client tokenization), use the document model for
chunks and the query model for search queries, restore input order from each item's
``index``, batch large inputs, and raise on any failed or short response.
"""

import pytest

from src.data_manager.vectorstore import remote_embeddings as mod
from src.data_manager.vectorstore.remote_embeddings import RemoteEmbeddings


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


def _fake_post(calls, *, shuffle=False, status=200, drop=0):
    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        items = [
            {"index": i, "embedding": [float(len(t)), float(i)]}
            for i, t in enumerate(json["input"])
        ]
        if shuffle:
            items = list(reversed(items))
        if drop:
            items = items[:-drop]
        return _Resp(status, {"data": items})

    return post


def test_documents_use_document_model_and_plain_text(monkeypatch):
    calls = []
    monkeypatch.setattr(mod.requests, "post", _fake_post(calls))
    emb = RemoteEmbeddings(
        base_url="http://h:8011/v1", model="m-doc", query_model="m-query"
    )

    vectors = emb.embed_documents(["alpha", "be"])

    assert calls[0]["url"] == "http://h:8011/v1/embeddings"
    assert calls[0]["json"] == {"model": "m-doc", "input": ["alpha", "be"]}
    assert vectors == [[5.0, 0.0], [2.0, 1.0]]


def test_query_uses_query_model(monkeypatch):
    calls = []
    monkeypatch.setattr(mod.requests, "post", _fake_post(calls))
    emb = RemoteEmbeddings(base_url="http://h/v1/", model="m-doc", query_model="m-q")

    assert emb.embed_query("hi") == [2.0, 0.0]
    assert calls[0]["url"] == "http://h/v1/embeddings"
    assert calls[0]["json"] == {"model": "m-q", "input": ["hi"]}


def test_query_falls_back_to_model(monkeypatch):
    calls = []
    monkeypatch.setattr(mod.requests, "post", _fake_post(calls))
    RemoteEmbeddings(base_url="http://h/v1", model="m-doc").embed_query("x")
    assert calls[0]["json"]["model"] == "m-doc"


def test_response_order_restored_by_index(monkeypatch):
    calls = []
    monkeypatch.setattr(mod.requests, "post", _fake_post(calls, shuffle=True))
    vectors = RemoteEmbeddings(base_url="http://h/v1", model="m").embed_documents(
        ["a", "bb", "ccc"]
    )
    assert [v[1] for v in vectors] == [0.0, 1.0, 2.0]


def test_documents_are_batched(monkeypatch):
    calls = []
    monkeypatch.setattr(mod.requests, "post", _fake_post(calls))
    emb = RemoteEmbeddings(base_url="http://h/v1", model="m", batch_size=2)

    vectors = emb.embed_documents(["a", "bb", "ccc", "dddd", "eeeee"])

    assert [c["json"]["input"] for c in calls] == [
        ["a", "bb"],
        ["ccc", "dddd"],
        ["eeeee"],
    ]
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_empty_input_makes_no_request(monkeypatch):
    calls = []
    monkeypatch.setattr(mod.requests, "post", _fake_post(calls))
    assert RemoteEmbeddings(base_url="http://h/v1", model="m").embed_documents([]) == []
    assert calls == []


def test_api_key_and_timeout_are_sent(monkeypatch):
    calls = []
    monkeypatch.setattr(mod.requests, "post", _fake_post(calls))
    RemoteEmbeddings(
        base_url="http://h/v1", model="m", api_key="k", timeout=7.5
    ).embed_query("x")
    assert calls[0]["headers"]["Authorization"] == "Bearer k"
    assert calls[0]["timeout"] == 7.5


def test_non_2xx_raises(monkeypatch):
    monkeypatch.setattr(mod.requests, "post", _fake_post([], status=503))
    with pytest.raises(RuntimeError, match="503"):
        RemoteEmbeddings(base_url="http://h/v1", model="m").embed_documents(["a"])


def test_short_response_raises(monkeypatch):
    monkeypatch.setattr(mod.requests, "post", _fake_post([], drop=1))
    with pytest.raises(RuntimeError, match="2 inputs"):
        RemoteEmbeddings(base_url="http://h/v1", model="m").embed_documents(["a", "b"])
