"""Tests for title/source header injection during ingestion.

Covers the ``document-ingestion`` spec for the add-title-aware-retrieval change:
the searchable text of every chunk includes the document title (``display_name``)
and filename, the header is configurable, and stemming is applied symmetrically.
"""

import types
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.data_manager.vectorstore import manager as manager_module
from src.data_manager.vectorstore.manager import VectorStoreManager

# --- _build_title_source_header (task 1.1) ---------------------------------


def test_header_uses_display_name_as_title():
    header = VectorStoreManager._build_title_source_header(
        "Quantum Computing Primer", "qc_primer.txt"
    )
    assert header == "Title: Quantum Computing Primer\nSource: qc_primer.txt\n\n"


def test_header_falls_back_to_filename_stem_when_display_name_missing():
    header = VectorStoreManager._build_title_source_header(None, "weekly_report.pdf")
    assert header == "Title: weekly_report\nSource: weekly_report.pdf\n\n"


def test_header_falls_back_when_display_name_blank():
    header = VectorStoreManager._build_title_source_header("   ", "notes.md")
    assert header == "Title: notes\nSource: notes.md\n\n"


def test_header_ends_with_blank_line_separator():
    header = VectorStoreManager._build_title_source_header("Doc", "doc.txt")
    assert header.endswith("\n\n")
    assert header.startswith("Title: ")
    assert "\nSource: " in header


# --- ingestion path (tasks 1.2 - 1.4) --------------------------------------


class _InlineFuture:
    def __init__(self, fn, *args, **kwargs):
        self._exc = None
        self._result = None
        try:
            self._result = fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive
            self._exc = exc

    def result(self):
        if self._exc:  # pragma: no cover - defensive
            raise self._exc
        return self._result


class _InlineExecutor:
    def __init__(self, max_workers=1):
        self.max_workers = max_workers
        self.futures = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, *args, **kwargs):
        fut = _InlineFuture(fn, *args, **kwargs)
        self.futures.append(fut)
        return fut


def _run_ingestion(
    monkeypatch,
    *,
    bodies,
    display_name=None,
    data_manager_config=None,
    stemmer=None,
    filename="doc.txt",
):
    """Run ``_add_to_postgres`` for a single file and return inserted chunk_text.

    ``bodies`` is the list of chunk body strings the splitter yields. The return
    value is the list of ``chunk_text`` strings actually inserted into Postgres.
    """
    manager = VectorStoreManager.__new__(VectorStoreManager)
    manager.parallel_workers = 1
    manager.collection_name = "test_collection"
    manager._data_manager_config = data_manager_config or {
        "stemming": {"enabled": False}
    }
    manager._pg_config = {"host": "localhost"}
    manager.stemmer = stemmer

    catalog = MagicMock()
    catalog.get_document_id.return_value = 1
    metadata = {"display_name": display_name} if display_name is not None else {}
    catalog.get_metadata_for_hash.return_value = metadata
    manager._catalog = catalog

    split_docs = [SimpleNamespace(page_content=body, metadata={}) for body in bodies]
    manager.text_splitter = SimpleNamespace(split_documents=lambda docs: split_docs)
    manager.embedding_model = SimpleNamespace(
        embed_documents=lambda chunks: [[0.1, 0.2, 0.3] for _ in chunks]
    )
    manager.loader = lambda _path: SimpleNamespace(load=lambda: split_docs)

    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    captured = []

    def _capture_execute_values(cursor, sql, data, template=None):
        # insert_data tuples are (document_id, idx, chunk_text, embedding, meta).
        captured.extend(row[2] for row in data)

    # Use a simple whitespace tokenizer so the stemming path is deterministic
    # without requiring nltk's punkt data to be downloaded.
    fake_nltk = types.SimpleNamespace(
        tokenize=types.SimpleNamespace(word_tokenize=lambda text: text.split())
    )
    monkeypatch.setattr(manager_module, "nltk", fake_nltk)

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(
        manager_module.psycopg2.extras, "execute_values", _capture_execute_values
    )
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    manager._add_to_postgres({"hash-1": f"/tmp/{filename}"})
    return captured


def test_chunks_include_title_tokens_when_keyword_only_in_title(monkeypatch):
    # Keyword "Photosynthesis" appears only in the title, never the body.
    chunks = _run_ingestion(
        monkeypatch,
        bodies=["cellular respiration overview"],
        display_name="Photosynthesis Guide",
        filename="bio.txt",
    )
    assert chunks
    assert all("Photosynthesis" in chunk for chunk in chunks)
    assert all("bio.txt" in chunk for chunk in chunks)


def test_header_applied_to_every_chunk(monkeypatch):
    chunks = _run_ingestion(
        monkeypatch,
        bodies=["first part", "second part", "third part"],
        display_name="Multi Chunk Doc",
        filename="multi.txt",
    )
    assert len(chunks) == 3
    for chunk in chunks:
        assert chunk.startswith("Title: Multi Chunk Doc\nSource: multi.txt\n\n")


def test_header_disabled_preserves_body_only(monkeypatch):
    chunks = _run_ingestion(
        monkeypatch,
        bodies=["just the body text"],
        display_name="Should Not Appear",
        data_manager_config={
            "stemming": {"enabled": False},
            "title_header": {"enabled": False},
        },
        filename="plain.txt",
    )
    assert chunks == ["just the body text"]
    assert all("Title:" not in chunk for chunk in chunks)


def test_stemming_applied_symmetrically_to_header(monkeypatch):
    # A marker stemmer proves the SAME stem function ran over header and body.
    marker_stemmer = SimpleNamespace(stem=lambda w: f"{w}_S")
    chunks = _run_ingestion(
        monkeypatch,
        bodies=["running tests"],
        display_name="Photosynthesis",
        data_manager_config={
            "stemming": {"enabled": True},
            "title_header": {"enabled": True},
        },
        stemmer=marker_stemmer,
        filename="bio.txt",
    )
    assert chunks
    chunk = chunks[0]
    # Header tokens were stemmed (carry the marker), not left verbatim.
    assert "Title:_S" in chunk
    assert "Photosynthesis_S" in chunk
    # Body tokens were stemmed with the same stemmer.
    assert "running_S" in chunk
    assert "tests_S" in chunk
