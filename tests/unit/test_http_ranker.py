"""Unit tests for ``HttpRanker``, the HTTP reranker backend.

It exposes FlashRank's ``rerank(request)`` contract (``request.query`` and
``request.passages`` of ``{"id", "text"}``; returns dicts with ``id`` and ``score``,
best first) so the hierarchical retriever can use it unchanged.
"""

from types import SimpleNamespace

import pytest

from src.data_manager.vectorstore.retrievers import http_ranker as mod
from src.data_manager.vectorstore.retrievers.http_ranker import (
    FallbackRanker,
    HttpRanker,
)


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


def _request(texts):
    return SimpleNamespace(
        query="how do I run a gpu job",
        passages=[{"id": 10 + i, "text": t, "meta": {}} for i, t in enumerate(texts)],
    )


def test_posts_model_query_and_documents(monkeypatch):
    calls = []

    def post(url, json=None, timeout=None, headers=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _Resp(200, {"results": [{"index": 0, "relevance_score": 0.5}]})

    monkeypatch.setattr(mod.requests, "post", post)
    HttpRanker(url="http://h:8012/v1/", model="rr", timeout=9.0).rerank(_request(["a"]))

    assert calls == [
        {
            "url": "http://h:8012/v1/rerank",
            "json": {
                "model": "rr",
                "query": "how do I run a gpu job",
                "documents": ["a"],
            },
            "timeout": 9.0,
        }
    ]


def test_scores_map_back_to_passage_ids_best_first(monkeypatch):
    results = [
        {"index": 0, "relevance_score": 0.1},
        {"index": 2, "relevance_score": 0.9},
        {"index": 1, "relevance_score": 0.4},
    ]
    monkeypatch.setattr(
        mod.requests, "post", lambda *a, **k: _Resp(200, {"results": results})
    )

    ranked = HttpRanker(url="http://h/v1", model="rr").rerank(_request(["x", "y", "z"]))

    assert [(r["id"], r["score"]) for r in ranked] == [(12, 0.9), (11, 0.4), (10, 0.1)]
    assert ranked[0]["text"] == "z"


def test_empty_passages_make_no_request(monkeypatch):
    def post(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("no request expected")

    monkeypatch.setattr(mod.requests, "post", post)
    assert HttpRanker(url="http://h/v1", model="rr").rerank(_request([])) == []


def test_non_2xx_raises(monkeypatch):
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp(500, {}))
    with pytest.raises(RuntimeError, match="500"):
        HttpRanker(url="http://h/v1", model="rr").rerank(_request(["a"]))


def test_missing_scores_raise(monkeypatch):
    monkeypatch.setattr(
        mod.requests,
        "post",
        lambda *a, **k: _Resp(200, {"results": [{"index": 0, "relevance_score": 1.0}]}),
    )
    with pytest.raises(RuntimeError, match="2 candidates"):
        HttpRanker(url="http://h/v1", model="rr").rerank(_request(["a", "b"]))


# --- FallbackRanker -----------------------------------------------------------


class _StubRanker:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.requests = []

    def rerank(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def test_fallback_ranker_uses_the_primary_when_it_succeeds():
    primary = _StubRanker(result=[{"id": 10, "score": 0.9}])
    built = []
    ranker = FallbackRanker(primary, lambda: built.append(1) or _StubRanker())
    assert ranker.rerank(_request(["a"])) == [{"id": 10, "score": 0.9}]
    assert built == []  # FlashRank is never loaded while the server answers


def test_fallback_ranker_reranks_with_flashrank_when_the_primary_fails(caplog):
    request = _request(["a", "b"])
    fallback = _StubRanker(result=[{"id": 11, "score": 0.7}, {"id": 10, "score": 0.2}])
    ranker = FallbackRanker(
        _StubRanker(error=RuntimeError("rerank request to http://h failed: HTTP 503")),
        lambda: fallback,
    )
    with caplog.at_level("WARNING"):
        assert ranker.rerank(request) == [
            {"id": 11, "score": 0.7},
            {"id": 10, "score": 0.2},
        ]
    assert fallback.requests == [request]
    assert "falling back to FlashRank" in caplog.text
    assert "HTTP 503" in caplog.text
