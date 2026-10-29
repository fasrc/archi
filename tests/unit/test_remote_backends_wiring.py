"""Wiring for the opt-in remote backends: the embedding class registry, the
retriever factory's ``reranker.backend`` switch, and the base-config render.

Defaults must stay exactly as before; the new pieces appear only when configured.
"""

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape
from langchain_core.vectorstores import VectorStore

from src.data_manager.vectorstore.remote_embeddings import RemoteEmbeddings
from src.data_manager.vectorstore.retrievers import build_vector_retriever
from src.data_manager.vectorstore.retrievers.http_ranker import HttpRanker
from src.utils.config_service import ConfigService


class _FakeStore(VectorStore):
    def hybrid_search(self, query, k, semantic_weight, bm25_weight):  # pragma: no cover
        return []

    def _get_connection(self):  # pragma: no cover
        return None

    def _close_connection(self, conn):  # pragma: no cover
        return None

    def similarity_search(self, query, k=4, **kwargs):  # pragma: no cover
        return []

    @classmethod
    def from_texts(cls, texts, embedding, metadatas=None, **kwargs):  # pragma: no cover
        return cls()


def _render(data_manager):
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    rendered = env.get_template("base-config.yaml").render(
        verbosity=0, data_manager=data_manager
    )
    return yaml.safe_load(rendered)["data_manager"]


# --- embedding class registry -------------------------------------------------


def test_remote_embeddings_resolves_to_its_class():
    resolved = ConfigService._resolve_embedding_classes(
        {"RemoteEmbeddings": {"class": "RemoteEmbeddings", "kwargs": {"model": "m"}}}
    )
    assert resolved["RemoteEmbeddings"]["class"] is RemoteEmbeddings


def test_remote_embeddings_resolves_by_entry_name_without_class():
    resolved = ConfigService._resolve_embedding_classes({"RemoteEmbeddings": {}})
    assert resolved["RemoteEmbeddings"]["class"] is RemoteEmbeddings


# --- retriever factory --------------------------------------------------------


def _hier(reranker):
    return {"hierarchical_rerank": {"enabled": True, "reranker": reranker}}


def test_http_backend_injects_http_ranker():
    retriever = build_vector_retriever(
        _FakeStore(),
        _hier(
            {"backend": "http", "url": "http://h:8012/v1", "model": "rr", "timeout": 30}
        ),
    )
    assert isinstance(retriever.reranker, HttpRanker)
    assert retriever.reranker.url == "http://h:8012/v1"
    assert retriever.reranker.model == "rr"
    assert retriever.reranker.timeout == 30
    assert retriever.reranker_model == "rr"


def test_http_backend_without_url_raises():
    with pytest.raises(ValueError, match="reranker.url"):
        build_vector_retriever(_FakeStore(), _hier({"backend": "http", "model": "rr"}))


def test_unknown_backend_raises():
    with pytest.raises(ValueError, match="reranker.backend"):
        build_vector_retriever(_FakeStore(), _hier({"backend": "gpu"}))


def test_default_backend_keeps_flashrank():
    retriever = build_vector_retriever(
        _FakeStore(), _hier({"model": "ms-marco-MiniLM-L-12-v2"})
    )
    assert retriever.reranker is None  # FlashRank Ranker is built lazily by model name
    assert retriever.reranker_model == "ms-marco-MiniLM-L-12-v2"


# --- base-config render -------------------------------------------------------


def test_remote_embeddings_absent_by_default():
    assert "RemoteEmbeddings" not in _render({})["embedding_class_map"]


def test_remote_embeddings_rendered_with_typed_kwargs_and_dimensions():
    dm = _render(
        {
            "embedding_name": "RemoteEmbeddings",
            "embedding_class_map": {
                "RemoteEmbeddings": {
                    "kwargs": {
                        "base_url": "http://localhost:8011/v1",
                        "model": "nemotron-embed:document",
                        "query_model": "nemotron-embed:query",
                        "batch_size": 16,
                        "timeout": 120.0,
                    },
                    "dimensions": 1024,
                }
            },
        }
    )
    entry = dm["embedding_class_map"]["RemoteEmbeddings"]
    assert dm["embedding_name"] == "RemoteEmbeddings"
    assert entry["class"] == "RemoteEmbeddings"
    assert entry["kwargs"] == {
        "base_url": "http://localhost:8011/v1",
        "model": "nemotron-embed:document",
        "query_model": "nemotron-embed:query",
        "batch_size": 16,
        "timeout": 120.0,
    }
    assert entry["dimensions"] == 1024
    # Existing entries are untouched.
    assert (
        dm["embedding_class_map"]["HuggingFaceEmbeddings"]["class"]
        == "HuggingFaceEmbeddings"
    )


def test_reranker_backend_defaults_to_flashrank_without_url():
    reranker = _render({})["retrievers"]["hierarchical_rerank"]["reranker"]
    assert reranker["backend"] == "flashrank"
    assert "url" not in reranker
    assert reranker["model"] == "ms-marco-MiniLM-L-12-v2"


def test_reranker_http_backend_rendered_with_url():
    reranker = _render(
        {
            "retrievers": {
                "hierarchical_rerank": {
                    "reranker": {
                        "backend": "http",
                        "url": "http://localhost:8012/v1",
                        "model": "nemotron-rerank",
                        "timeout": 60,
                    }
                }
            }
        }
    )["retrievers"]["hierarchical_rerank"]["reranker"]
    assert reranker == {
        "backend": "http",
        "url": "http://localhost:8012/v1",
        "model": "nemotron-rerank",
        "timeout": 60,
    }
