"""Re-ingest under an unchanged hash AND unchanged filename must still refresh
stale chunks when the document's *content* changed (issue #39).

Resource hashes are identity-based (``md5(url)`` / ``md5(path)``), so a re-scraped
page at the same URL or an in-place local/ticket overwrite keeps the SAME hash. The
HTML->Markdown work in #38 only catches a changed *basename* (``page.html`` ->
``page.md``); a content-only rewrite under the SAME filename slips through and the
old chunks are served indefinitely. ``_collect_stale_hashes`` must additionally
compare a persisted per-document *content signal* against the current on-disk file.

RED: the content-signal path (tasks 2-3) is not implemented yet, so the test below
is marked ``xfail(strict=True)`` — it fails on today's filename-only detection and
will start passing once ``_collect_stale_hashes`` consults the content signal, at
which point the strict marker fails the gate and must be removed in the GREEN phase.
"""

import hashlib
import importlib.util
import sys
import types
from types import SimpleNamespace

import pytest


def _missing(module_name: str) -> bool:
    """True if a module is genuinely not installed (so a stub is safe).

    Guarding on real availability keeps these stubs NON-DESTRUCTIVE: when langchain
    is actually present (as in CI, via requirements-base), we never shadow the real
    package — otherwise a bare ``langchain_core`` stub here would break a sibling
    test's ``from langchain_core.messages import ...`` regardless of collection order.
    """
    if module_name in sys.modules:
        return False
    try:
        return importlib.util.find_spec(module_name) is None
    except (ImportError, ValueError):
        return True


# Minimal stubs so the module imports without langchain/nltk installed (CI parity),
# applied ONLY when the real package is absent.
if _missing("langchain_core"):
    sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
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
if _missing("nltk"):
    nltk_module = types.ModuleType("nltk")
    nltk_module.tokenize = SimpleNamespace(word_tokenize=lambda text: text.split())
    nltk_module.stem = SimpleNamespace(
        PorterStemmer=lambda: SimpleNamespace(stem=lambda w: w)
    )
    nltk_module.download = lambda *_a, **_k: None
    sys.modules["nltk"] = nltk_module
if _missing("langchain_text_splitters"):
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
if _missing("langchain_community"):
    sys.modules.setdefault(
        "langchain_community", types.ModuleType("langchain_community")
    )
if _missing("langchain_community.document_loaders"):
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


@pytest.mark.xfail(
    strict=True,
    reason="RED (issue #39): content-signal staleness not implemented yet "
    "(tasks 2-3). Remove this marker once _collect_stale_hashes compares the "
    "persisted content signal against the current on-disk file.",
)
def test_collect_stale_hashes_detects_changed_content_same_filename(
    monkeypatch, tmp_path
):
    """A re-ingested document whose CONTENT changed but whose filename and identity
    hash are UNCHANGED is reported stale via its content signal.

    Filename-only detection cannot see this: the basename ``page.md`` is identical to
    what was embedded, so the conversion-extension heuristic returns "not stale". The
    content signal recorded at the previous ingest no longer matches the bytes now on
    disk, which is the only evidence the content was rewritten in place.
    """
    mgr = _manager()

    # Filename is UNCHANGED -> the #38 basename heuristic alone says "not stale".
    monkeypatch.setattr(mgr, "_collect_embedded_filenames", lambda: {"h1": {"page.md"}})
    # Content signal persisted at the PREVIOUS ingest (tasks 2-3 add this collector).
    monkeypatch.setattr(
        mgr,
        "_collect_embedded_content_signals",
        lambda: {"h1": {"signal-from-the-previous-ingest"}},
        raising=False,
    )

    # The on-disk file now holds NEW content under the SAME basename: its freshly
    # computed content signal cannot equal the stale recorded one.
    page = tmp_path / "page.md"
    page.write_text("brand new body text after an in-place re-scrape")
    files_in_data = {"h1": str(page)}

    stale = mgr._collect_stale_hashes(files_in_data, {"h1"})

    assert stale == {"h1"}


def test_collect_stale_hashes_unchanged_content_is_not_stale(monkeypatch, tmp_path):
    """The no-change fast path is preserved: identical content + filename + hash
    yields an EMPTY stale set, so nothing is removed or re-embedded.

    This passes TODAY (filename-only detection sees an unchanged basename) and MUST
    keep passing once the content signal is consulted: the signal recorded at the
    previous ingest equals the signal freshly computed from the unchanged on-disk
    bytes, so the content check also reports "not stale". An empty stale set is the
    precondition for the ``hashes_in_data == hashes_in_vstore and not stale_hashes``
    short-circuit in ``update_vectorstore`` that skips all removal/re-embed work.
    """
    mgr = _manager()

    # On-disk file is UNCHANGED since the previous ingest.
    page = tmp_path / "page.md"
    page.write_text("the original body text, byte-for-byte unchanged")
    files_in_data = {"h1": str(page)}

    # Filename is UNCHANGED -> the #38 basename heuristic says "not stale".
    monkeypatch.setattr(mgr, "_collect_embedded_filenames", lambda: {"h1": {"page.md"}})
    # Content signal persisted at the previous ingest MATCHES the current on-disk
    # bytes. The GREEN content-signal computation (tasks 2-3) MUST hash the file
    # bytes the same way for an unchanged document to compare equal.
    embedded_signal = hashlib.md5(page.read_bytes()).hexdigest()
    monkeypatch.setattr(
        mgr,
        "_collect_embedded_content_signals",
        lambda: {"h1": {embedded_signal}},
        raising=False,
    )

    stale = mgr._collect_stale_hashes(files_in_data, {"h1"})

    assert stale == set()
