"""Both ingest construction sites must build persistence via the shared factory.

The spec mandates UI uploads are not bypassed: ``DataManager`` (scheduled/startup)
and the uploader UI must construct their persistence through ``build_persistence`` so
the processing pipeline applies uniformly. Importing those modules in CI pulls heavy
deps (nltk, langchain text splitters) that aren't installed, so the wiring contract is
asserted at the source level — a regression to a bare ``PersistenceService`` (which
would bypass processing) is caught either way.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def _normalize(text: str) -> str:
    """Drop all whitespace so assertions survive any line wrapping / spacing."""
    return re.sub(r"\s+", "", text)


_EXPECTED_CALL = (
    "self.persistence=build_persistence(self.config,self.data_path,self.pg_config)"
)


def test_data_manager_constructs_persistence_via_factory():
    source = _read("src/data_manager/data_manager.py")
    assert (
        "from src.data_manager.collectors.processing import build_persistence" in source
    )
    assert _EXPECTED_CALL in _normalize(source)
    # The bare service must not be constructed directly at this ingest site.
    assert "PersistenceService(self.data_path" not in source


def test_uploader_app_constructs_persistence_via_factory():
    source = _read("src/interfaces/uploader_app/app.py")
    assert (
        "from src.data_manager.collectors.processing import build_persistence" in source
    )
    assert _EXPECTED_CALL in _normalize(source)
    assert "PersistenceService(self.data_path" not in source


def test_chat_app_document_utils_stays_unwrapped():
    """The delete-only chat-app path intentionally uses the bare service."""
    source = _read("src/interfaces/chat_app/document_utils.py")
    assert "PersistenceService(sources_path" in source
    assert "build_persistence" not in source
