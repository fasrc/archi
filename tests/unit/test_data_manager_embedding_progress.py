"""DataManager.run_ingestion threads embedding_progress to update_vectorstore."""

import importlib.util
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock


def _missing(module_name: str) -> bool:
    if module_name in sys.modules:
        return False
    try:
        return importlib.util.find_spec(module_name) is None
    except (ImportError, ValueError):
        return True


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

from src.data_manager.data_manager import DataManager


def _bare_data_manager():
    dm = DataManager.__new__(DataManager)
    dm.localfile_manager = MagicMock()
    dm.scraper_manager = MagicMock()
    dm.ticket_manager = MagicMock()
    catalog = SimpleNamespace(refresh=lambda: None, file_index={})
    dm.persistence = SimpleNamespace(catalog=catalog, flush_index=lambda: None)
    dm.vector_manager = MagicMock()
    return dm


def test_run_ingestion_passes_embedding_progress_to_update_vectorstore():
    dm = _bare_data_manager()
    cb = object()
    dm.run_ingestion(embedding_progress=cb)
    dm.vector_manager.update_vectorstore.assert_called_once_with(embedding_progress=cb)


def test_run_ingestion_passes_none_when_no_embedding_progress():
    dm = _bare_data_manager()
    dm.run_ingestion()
    dm.vector_manager.update_vectorstore.assert_called_once_with(
        embedding_progress=None
    )
