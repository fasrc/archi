"""Corpus provenance for a QA run (#570).

The readings bracket the answering phase, the only phase that retrieves:
``start_readings`` runs the start guard and takes the first reading,
``end_readings`` takes the second, and scoring copies both. A spec without the
search tool retrieves nothing, so it records nulls and opens no connection.
"""

from types import SimpleNamespace

import pytest

import src.evaluation.qa.provenance as provenance
from src.utils.benchmark_provenance import CollectionNotReadyError

CONFIG = {
    "data_manager": {
        "collection_name": "fasrc",
        "embedding_name": "HuggingFaceEmbeddings",
        "embedding_class_map": {
            "HuggingFaceEmbeddings": {"kwargs": {"model_name": "Qwen/Q"}}
        },
    },
    "services": {"postgres": {"host": "db", "port": 5433, "database": "archi"}},
}
SEARCH = SimpleNamespace(tools=["search_vectorstore_hybrid"])
NO_SEARCH = SimpleNamespace(tools=["fetch_url"])
READY = {
    "chunk_count": 3,
    "usable_chunk_count": 3,
    "untagged_chunk_count": 0,
    "embedding_model_source": "chunks",
}


@pytest.fixture
def db(monkeypatch):
    state = {"pools": 0, "digest": "sha256/v2:aaa", "ready": READY}

    def pool_factory(config):
        state["pools"] += 1
        return object()

    def readiness(pool, identity):
        if isinstance(state["ready"], Exception):
            raise state["ready"]
        return state["ready"]

    def fingerprint(pool, config):
        if isinstance(state["digest"], Exception):
            raise state["digest"]
        return state["digest"]

    monkeypatch.setattr(provenance, "direct_pool", pool_factory)
    monkeypatch.setattr(provenance, "collection_readiness", readiness)
    monkeypatch.setattr(provenance, "live_corpus_fingerprint", fingerprint)
    return state


def test_start_records_the_identity_and_the_first_reading(db):
    readings = provenance.start_readings(CONFIG, SEARCH)
    assert readings["corpus_fingerprint_before"] == "sha256/v2:aaa"
    assert readings["retrieval_identity"] == {
        "collection": "fasrc_with_HuggingFaceEmbeddings",
        "embedding_name": "HuggingFaceEmbeddings",
        "embedding_model": "Qwen/Q",
        **READY,
    }


def test_the_guard_stops_the_run(db):
    db["ready"] = CollectionNotReadyError("empty")
    with pytest.raises(CollectionNotReadyError):
        provenance.start_readings(CONFIG, SEARCH)


def test_without_the_search_tool_nothing_is_read(db):
    assert provenance.start_readings(CONFIG, NO_SEARCH) == {
        "retrieval_identity": None,
        "corpus_fingerprint_before": None,
    }
    assert provenance.end_readings(CONFIG, NO_SEARCH, None) == {
        "corpus_fingerprint": None,
        "corpus_unchanged_at_endpoints": None,
    }
    assert db["pools"] == 0


def test_end_compares_with_the_first_reading(db):
    assert provenance.end_readings(CONFIG, SEARCH, "sha256/v2:aaa") == {
        "corpus_fingerprint": "sha256/v2:aaa",
        "corpus_unchanged_at_endpoints": True,
    }
    db["digest"] = "sha256/v2:bbb"
    assert (
        provenance.end_readings(CONFIG, SEARCH, "sha256/v2:aaa")[
            "corpus_unchanged_at_endpoints"
        ]
        is False
    )


def test_a_failed_reading_is_a_marker_and_unknown_stability(db):
    db["digest"] = RuntimeError("refused")
    readings = provenance.end_readings(CONFIG, SEARCH, "sha256/v2:aaa")
    assert readings["corpus_fingerprint"].startswith("<unavailable:")
    assert "refused" in readings["corpus_fingerprint"]
    assert readings["corpus_unchanged_at_endpoints"] is None


def test_summary_fields_copy_the_manifest():
    manifest = {
        "retrieval_identity": {"collection": "c"},
        "corpus_fingerprint_before": "sha256/v2:a",
        "corpus_fingerprint": "sha256/v2:a",
        "corpus_unchanged_at_endpoints": True,
        "other": 1,
    }
    assert provenance.summary_fields(manifest) == {
        "retrieval_identity": {"collection": "c"},
        "corpus_fingerprint_before": "sha256/v2:a",
        "corpus_fingerprint": "sha256/v2:a",
        "corpus_unchanged_at_endpoints": True,
    }
    assert provenance.summary_fields({}) == dict.fromkeys(provenance.PROVENANCE_KEYS)


def test_the_direct_pool_uses_the_connector_parameters(monkeypatch):
    opened = []

    class Connection:
        def close(self):
            opened.append("closed")

    monkeypatch.setattr(
        provenance.psycopg2,
        "connect",
        lambda **params: opened.append(params) or Connection(),
    )
    import src.archi.utils.vectorstore_connector as connector

    monkeypatch.setattr(connector, "read_secret", lambda name: "pw")

    with provenance.direct_pool(CONFIG).get_connection():
        pass

    assert opened == [
        {
            "host": "db",
            "port": 5433,
            "user": "postgres",
            "password": "pw",
            "dbname": "archi",
        },
        "closed",
    ]
