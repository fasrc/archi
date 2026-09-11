"""
Unit tests for the relevance score the LLM sees during retriever tool calls.

The default retrieval path (``hierarchical_rerank.enabled: true``) uses
``LlamaIndexHierarchicalRetriever``, whose ``_get_relevant_documents`` returns a
plain ``List[Document]`` and records the cross-encoder score on
``doc.metadata["rerank_score"]``. ``BaseRetriever.invoke()`` is contractually
``List[Document]``, so the score cannot ride along as a tuple the way
``HybridRetriever`` supplies it. ``_normalize_results`` must therefore read the
score off metadata, or the model is shown ``Score: n/a`` for every document on
the default path (issue #464).
"""

import math

from langchain_core.documents import Document

from src.archi.pipelines.agents.tools.retriever import (
    _format_documents_for_llm,
    _normalize_results,
)


def _doc(**metadata):
    """A Document carrying only the metadata under test."""
    base = {
        "title": "Running Jobs",
        "url": "https://docs.rc.fas.harvard.edu/kb/running-jobs",
        "resource_hash": "312440328619",
    }
    base.update(metadata)
    return Document(page_content="Jobs on sapphire have a 3-day limit.", metadata=base)


def _render(results):
    return _format_documents_for_llm(
        _normalize_results(results), max_documents=4, max_chars=200
    )


class TestBareDocumentScoreComesFromMetadata:
    """The default (hierarchical rerank) path hands the formatter bare Documents."""

    def test_bare_document_renders_its_rerank_score(self):
        out = _render([_doc(rerank_score=0.9952131509780884)])
        assert "Score: 0.9952" in out
        assert "Score: n/a" not in out

    def test_bare_document_without_a_rerank_score_still_renders_n_a(self):
        out = _render([_doc()])
        assert "Score: n/a" in out

    def test_an_integer_rerank_score_renders(self):
        out = _render([_doc(rerank_score=1)])
        assert "Score: 1.0000" in out


class TestTuplePathIsUnchanged:
    """HybridRetriever supplies (Document, score) tuples; that must not regress."""

    def test_tuple_score_still_renders(self):
        out = _render([(_doc(), 0.87)])
        assert "Score: 0.8700" in out

    def test_tuple_score_wins_over_a_metadata_score(self):
        # Explicit beats inferred: a retriever that states the score outranks one
        # that leaves it on metadata for us to find.
        out = _render([(_doc(rerank_score=0.11), 0.87)])
        assert "Score: 0.8700" in out
        assert "Score: 0.1100" not in out

    def test_a_tuple_carrying_no_score_falls_back_to_the_metadata_score(self):
        # A tuple whose score is None states nothing, so the metadata score is
        # still the best information available.
        out = _render([(_doc(rerank_score=0.42), None)])
        assert "Score: 0.4200" in out


class TestUnrecognisedItemsAreSkipped:
    """A retriever returning something else must not crash the tool."""

    def test_an_item_that_is_neither_document_nor_tuple_is_dropped(self):
        assert _normalize_results(["not a document", 42, None]) == []

    def test_a_usable_document_survives_alongside_an_unusable_item(self):
        doc = _doc(rerank_score=0.5)
        assert _normalize_results(["junk", doc]) == [(doc, 0.5)]


class TestUnusableMetadataScoresFallBackToNotAvailable:
    """A score we cannot render as a number must not reach the model as one."""

    def test_a_non_numeric_metadata_score_renders_n_a(self):
        out = _render([_doc(rerank_score="0.99")])
        assert "Score: n/a" in out

    def test_a_boolean_metadata_score_renders_n_a(self):
        # bool subclasses int, so a naive isinstance check would render
        # "Score: 1.0000" for True.
        out = _render([_doc(rerank_score=True)])
        assert "Score: n/a" in out

    def test_a_nan_metadata_score_renders_n_a(self):
        out = _render([_doc(rerank_score=float("nan"))])
        assert "Score: n/a" in out
        assert "nan" not in out.lower()

    def test_an_infinite_metadata_score_renders_n_a(self):
        out = _render([_doc(rerank_score=math.inf)])
        assert "Score: n/a" in out
        assert "inf" not in out.lower()

    def test_a_none_metadata_score_renders_n_a(self):
        out = _render([_doc(rerank_score=None)])
        assert "Score: n/a" in out
