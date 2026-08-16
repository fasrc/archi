"""
Unit tests for the enforced size ceilings on tool results (issue #235).

Both the preserve-count floor and the exemption floor in the in-loop context
budget are statements about *retained* tool results, so neither holds unless a
retained result has an enforced size. Today neither tool bounds what it returns:

* ``fetch_catalog_document`` takes ``max_chars`` as a **model-supplied tool
  argument** and forwards it unclamped; ``max_chars=0`` is falsy at
  ``uploader_app/app.py:769`` and disables truncation entirely, returning the
  whole document. It then appends a path and up to 800 characters of metadata
  preview *after* the server-limited text, so even a clamped request returns
  more than it asked for.
* ``search_vectorstore_hybrid``'s ``max_chars`` bounds ``doc.page_content``
  only. The rendered header interpolates ``title``, ``url`` and
  ``resource_hash`` straight from document metadata with no cap, so one
  document with a pathological title produces an arbitrarily large result.

These tests pin the *complete serialized return value* of each tool, not the
size it requests from its backend.
"""

import pytest
from langchain_core.documents import Document

from src.archi.pipelines.agents.tools.local_files import create_document_fetch_tool
from src.archi.pipelines.agents.tools.result_limits import (
    TRUNCATION_MARKER,
    clamp_result,
    resolve_requested_chars,
)
from src.archi.pipelines.agents.tools.retriever import create_retriever_tool


class _FakeCatalog:
    """Stands in for RemoteCatalogClient, recording what size was requested."""

    def __init__(self, text: str, metadata=None, path="websites/doc.md"):
        self._text = text
        self._metadata = metadata if metadata is not None else {"title": "Doc"}
        self._path = path
        self.requested_max_chars = None

    def get_document(self, resource_hash, *, max_chars=4000):
        self.requested_max_chars = max_chars
        # Mirror the endpoint's own semantics: it truncates only when max_chars
        # is truthy, which is exactly why 0 must never reach it.
        text = self._text
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
        return {
            "hash": resource_hash,
            "path": self._path,
            "metadata": self._metadata,
            "text": text,
        }


class _FakeRetriever:
    def __init__(self, docs):
        self._docs = docs

    def invoke(self, query):  # pragma: no cover - trivial
        return self._docs


CEILING = 2000


class TestDocumentFetchCeiling:
    """Task 2.1-2.4: the fetch tool bounds its complete serialized return."""

    def test_oversized_request_is_clamped_to_the_ceiling(self):
        catalog = _FakeCatalog("X" * 500_000)
        tool = create_document_fetch_tool(catalog, max_result_chars=CEILING)

        out = tool.invoke({"resource_hash": "abc", "max_chars": 200_000})

        assert len(out) <= CEILING

    def test_serialized_return_not_just_requested_text_is_bounded(self):
        """The path and metadata preview are appended *after* the text.

        Clamping only the value sent to the catalog leaves the assembled string
        over the limit, which is the defect this asserts against.
        """
        catalog = _FakeCatalog(
            "X" * 500_000,
            metadata={
                "title": "T" * 5_000,
                "url": "https://example.org/" + "u" * 5_000,
            },
        )
        tool = create_document_fetch_tool(catalog, max_result_chars=CEILING)

        out = tool.invoke({"resource_hash": "abc", "max_chars": CEILING})

        assert len(out) <= CEILING

    def test_zero_max_chars_does_not_disable_truncation(self):
        """``0`` is falsy: it must mean "use the ceiling", never "no limit"."""
        catalog = _FakeCatalog("X" * 500_000)
        tool = create_document_fetch_tool(catalog, max_result_chars=CEILING)

        out = tool.invoke({"resource_hash": "abc", "max_chars": 0})

        assert len(out) <= CEILING
        assert catalog.requested_max_chars, "0 must not reach the catalog client"

    @pytest.mark.parametrize("bad", [-1, -4000])
    def test_negative_max_chars_is_treated_as_the_ceiling(self, bad):
        """Negative sizes are the reachable invalid input.

        Non-integer values cannot get this far: the tool's pydantic schema types
        ``max_chars`` as ``int`` and rejects them at the boundary. They are
        covered directly on ``resolve_requested_chars``, which is also called
        from paths that do not have that schema in front of them.
        """
        catalog = _FakeCatalog("X" * 500_000)
        tool = create_document_fetch_tool(catalog, max_result_chars=CEILING)

        out = tool.invoke({"resource_hash": "abc", "max_chars": bad})

        assert len(out) <= CEILING
        assert catalog.requested_max_chars == CEILING

    def test_smaller_request_is_still_honoured(self):
        """Clamping must not flatten a legitimate smaller read."""
        catalog = _FakeCatalog("X" * 500_000)
        tool = create_document_fetch_tool(catalog, max_result_chars=CEILING)

        tool.invoke({"resource_hash": "abc", "max_chars": 100})

        assert catalog.requested_max_chars == 100

    def test_ordinary_result_is_untouched(self):
        catalog = _FakeCatalog("short body", metadata={"title": "Doc"})
        tool = create_document_fetch_tool(catalog, max_result_chars=CEILING)

        out = tool.invoke({"resource_hash": "abc", "max_chars": 4000})

        assert "short body" in out
        assert TRUNCATION_MARKER not in out


class TestRetrieverCeiling:
    """Task 2.5-2.6: the retriever bounds its complete serialized output."""

    def test_pathological_metadata_cannot_blow_the_ceiling(self):
        """``max_chars`` bounds page_content only; the header is uncapped."""
        doc = Document(
            page_content="tiny",
            metadata={
                "title": "T" * 100_000,
                "url": "https://example.org/" + "u" * 100_000,
                "resource_hash": "h" * 10_000,
                "filename": "f.md",
            },
        )
        tool = create_retriever_tool(
            _FakeRetriever([(doc, 0.9)]),
            name="search_vectorstore_hybrid",
            max_result_chars=CEILING,
        )

        out = tool.invoke({"query": "anything"})

        assert len(out) <= CEILING

    def test_ordinary_retrieval_output_is_unmodified(self):
        doc = Document(
            page_content="a normal chunk of documentation",
            metadata={
                "title": "Normal Doc",
                "url": "https://example.org/normal",
                "resource_hash": "abc123",
            },
        )
        tool = create_retriever_tool(
            _FakeRetriever([(doc, 0.9)]),
            name="search_vectorstore_hybrid",
            max_result_chars=CEILING,
        )

        out = tool.invoke({"query": "anything"})

        assert "a normal chunk of documentation" in out
        assert "Normal Doc" in out
        assert TRUNCATION_MARKER not in out


class TestClampHelper:
    """The shared primitive both tools use."""

    def test_under_the_limit_is_returned_unchanged(self):
        assert clamp_result("hello", 100) == "hello"

    def test_over_the_limit_is_marked_as_partial(self):
        out = clamp_result("Y" * 500, 100)
        assert len(out) <= 100
        assert out.endswith(TRUNCATION_MARKER)

    def test_result_including_marker_never_exceeds_the_limit(self):
        """The marker counts against the budget; it must not push past it."""
        for limit in (60, 80, 120, 400):
            out = clamp_result("Z" * 10_000, limit)
            assert len(out) <= limit, f"limit={limit} produced {len(out)}"

    @pytest.mark.parametrize("requested", [0, -5, "nope", None])
    def test_invalid_requested_size_resolves_to_the_ceiling(self, requested):
        assert resolve_requested_chars(requested, 4000) == 4000

    def test_oversized_requested_size_resolves_to_the_ceiling(self):
        assert resolve_requested_chars(999_999, 4000) == 4000

    def test_smaller_requested_size_is_preserved(self):
        assert resolve_requested_chars(250, 4000) == 250
