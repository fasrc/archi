"""Re-ingest under an unchanged hash must refresh stale chunks (D11).

Hashes are identity-based (URL/path), so an HTML->Markdown rewrite keeps the same
hash. ``update_vectorstore`` compares only hash *sets*, so without extra handling a
re-ingested-then-converted doc would keep its old HTML-flattened chunks. The manager
must detect changed content under an unchanged hash and refresh those chunks.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Minimal stubs so the module imports without langchain/nltk installed (CI parity).
for name in ("langchain_core",):
    sys.modules.setdefault(name, types.ModuleType(name))
if "langchain_core.documents" not in sys.modules:
    m = types.ModuleType("langchain_core.documents")
    m.Document = object
    sys.modules["langchain_core.documents"] = m
if "langchain_core.embeddings" not in sys.modules:
    m = types.ModuleType("langchain_core.embeddings")
    m.Embeddings = object
    sys.modules["langchain_core.embeddings"] = m
if "langchain_core.vectorstores" not in sys.modules:
    m = types.ModuleType("langchain_core.vectorstores")
    m.VectorStore = object
    sys.modules["langchain_core.vectorstores"] = m
if "nltk" not in sys.modules:
    nltk_module = types.ModuleType("nltk")
    nltk_module.tokenize = SimpleNamespace(word_tokenize=lambda text: text.split())
    nltk_module.stem = SimpleNamespace(
        PorterStemmer=lambda: SimpleNamespace(stem=lambda w: w)
    )
    nltk_module.download = lambda *_a, **_k: None
    sys.modules["nltk"] = nltk_module
sys.modules.setdefault(
    "langchain_text_splitters", types.ModuleType("langchain_text_splitters")
)
if "langchain_text_splitters.character" not in sys.modules:
    character_module = types.ModuleType("langchain_text_splitters.character")

    class _DummyCharacterTextSplitter:
        def __init__(self, *a, **k):
            pass

        def split_documents(self, docs):
            return docs

    character_module.CharacterTextSplitter = _DummyCharacterTextSplitter
    sys.modules["langchain_text_splitters.character"] = character_module
sys.modules.setdefault("langchain_community", types.ModuleType("langchain_community"))
if "langchain_community.document_loaders" not in sys.modules:
    loaders_module = types.ModuleType("langchain_community.document_loaders")

    class _DummyLoader:
        def __init__(self, *_a, **_k):
            pass

        def load(self):
            return []

    for attr in ("BSHTMLLoader", "PyPDFLoader", "PythonLoader", "TextLoader"):
        setattr(loaders_module, attr, _DummyLoader)
    sys.modules["langchain_community.document_loaders"] = loaders_module
if "langchain_community.document_loaders.text" not in sys.modules:
    text_module = types.ModuleType("langchain_community.document_loaders.text")
    text_module.TextLoader = sys.modules[
        "langchain_community.document_loaders"
    ].TextLoader
    sys.modules["langchain_community.document_loaders.text"] = text_module

from src.data_manager.vectorstore.manager import VectorStoreManager


def _manager():
    mgr = VectorStoreManager.__new__(VectorStoreManager)
    mgr.collection_name = "col"
    mgr._pg_config = {"host": "db"}
    mgr.data_path = "/data"
    return mgr


def test_stale_hash_is_removed_and_re_added(monkeypatch):
    """A hash present in both data and vstore but whose embedded filename changed
    (page.html -> page.md) is removed from the vstore and re-embedded."""
    mgr = _manager()

    files_in_data = {"h1": "/data/web/page.md", "h2": "/data/web/other.md"}
    monkeypatch.setattr(
        mgr, "fetch_collection", lambda: SimpleNamespace(count=lambda: 2)
    )
    monkeypatch.setattr(
        "src.data_manager.vectorstore.manager.PostgresCatalogService.load_sources_catalog",
        staticmethod(lambda data_path, pg: {"h1": "x", "h2": "y"}),
    )
    monkeypatch.setattr(
        mgr, "_collect_indexed_documents", lambda sources: files_in_data
    )
    # Both hashes already embedded.
    monkeypatch.setattr(mgr, "_collect_postgres_hashes", lambda: {"h1", "h2"})
    # h1 was embedded as page.html (now page.md on disk) -> stale; h2 unchanged.
    monkeypatch.setattr(
        mgr,
        "_collect_embedded_filenames",
        lambda: {"h1": "page.html", "h2": "other.md"},
    )

    removed = MagicMock()
    added = MagicMock()
    monkeypatch.setattr(mgr, "_remove_from_postgres", removed)
    monkeypatch.setattr(mgr, "_add_to_postgres", added)

    mgr.update_vectorstore()

    # h1's stale chunks deleted...
    removed.assert_called_once()
    assert removed.call_args[0][0] == ["h1"]
    # ...and h1 re-embedded with the new .md content.
    added.assert_called_once()
    assert set(added.call_args[0][0].keys()) == {"h1"}


def test_unchanged_corpus_does_not_refresh(monkeypatch):
    """When nothing changed, no chunks are removed or re-added."""
    mgr = _manager()

    files_in_data = {"h1": "/data/web/page.md"}
    monkeypatch.setattr(
        mgr, "fetch_collection", lambda: SimpleNamespace(count=lambda: 1)
    )
    monkeypatch.setattr(
        "src.data_manager.vectorstore.manager.PostgresCatalogService.load_sources_catalog",
        staticmethod(lambda data_path, pg: {"h1": "x"}),
    )
    monkeypatch.setattr(
        mgr, "_collect_indexed_documents", lambda sources: files_in_data
    )
    monkeypatch.setattr(mgr, "_collect_postgres_hashes", lambda: {"h1"})
    monkeypatch.setattr(mgr, "_collect_embedded_filenames", lambda: {"h1": "page.md"})

    removed = MagicMock()
    added = MagicMock()
    monkeypatch.setattr(mgr, "_remove_from_postgres", removed)
    monkeypatch.setattr(mgr, "_add_to_postgres", added)

    mgr.update_vectorstore()

    removed.assert_not_called()
    added.assert_not_called()


def test_collect_embedded_filenames_queries_document_chunks(monkeypatch):
    """The SQL body maps resource_hash -> embedded filename, skipping null rows
    and keeping the first filename seen per hash."""
    import src.data_manager.vectorstore.manager as manager_module

    mgr = _manager()

    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        ("h1", "page.html"),
        ("h1", "page-dupe.html"),  # duplicate hash -> first wins
        ("h2", "other.md"),
        (None, "orphan.md"),  # null hash -> skipped
        ("h3", None),  # null filename -> skipped
    ]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False
    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)

    result = mgr._collect_embedded_filenames()

    assert result == {"h1": "page.html", "h2": "other.md"}
    fake_conn.close.assert_called_once()


def test_collect_stale_hashes_no_candidates_skips_query(monkeypatch):
    mgr = _manager()
    called = {"n": 0}

    def _should_not_run():
        called["n"] += 1
        return {}

    monkeypatch.setattr(mgr, "_collect_embedded_filenames", _should_not_run)

    # No overlap between data hashes and vstore hashes -> no query, empty result.
    assert mgr._collect_stale_hashes({"h1": "/d/a.md"}, {"h9"}) == set()
    assert called["n"] == 0


def test_collect_stale_hashes_detects_only_changed_filenames(monkeypatch):
    mgr = _manager()
    monkeypatch.setattr(
        mgr,
        "_collect_embedded_filenames",
        lambda: {"h1": "page.html", "h2": "same.md"},
    )
    files_in_data = {
        "h1": "/data/web/page.md",  # changed (html -> md) => stale
        "h2": "/data/web/same.md",  # unchanged => not stale
        "h3": "/data/web/new.md",  # not embedded => ignored
    }
    stale = mgr._collect_stale_hashes(files_in_data, {"h1", "h2", "h3"})
    assert stale == {"h1"}


def test_collect_stale_hashes_query_failure_is_swallowed(monkeypatch):
    mgr = _manager()

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mgr, "_collect_embedded_filenames", _boom)
    # A query failure must not block ingest -> treat as "no stale hashes".
    assert mgr._collect_stale_hashes({"h1": "/d/a.md"}, {"h1"}) == set()
