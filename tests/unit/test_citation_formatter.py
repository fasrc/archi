"""
Unit tests for the citation formatter utility.
"""

from dataclasses import dataclass, field
from typing import Dict

import pytest

from src.archi.utils.citation_formatter import format_citations


@dataclass
class FakeDocument:
    """Minimal stand-in for a LangChain Document."""

    page_content: str = ""
    metadata: Dict = field(default_factory=dict)


class TestFormatCitationsEmpty:

    def test_no_sources_returns_empty_string(self):
        assert format_citations([], []) == ""

    def test_none_scores_returns_empty_string(self):
        assert format_citations([], None) == ""

    def test_documents_with_no_usable_metadata(self):
        docs = [FakeDocument(metadata={})]
        result = format_citations(docs, [0.5])
        assert result == ""


class TestFormatCitationsSingleSource:

    def test_single_source_with_display_name(self):
        docs = [FakeDocument(metadata={"display_name": "guide.md"})]
        result = format_citations(docs, [0.85])
        assert "---" in result
        assert "**Sources:**" in result
        assert "`guide.md`" in result
        assert "(relevance: 0.85)" in result

    def test_single_source_uses_source_key(self):
        docs = [FakeDocument(metadata={"source": "notes.txt"})]
        result = format_citations(docs, [0.42])
        assert "`notes.txt`" in result

    def test_single_source_uses_filename_key(self):
        docs = [FakeDocument(metadata={"filename": "report.pdf"})]
        result = format_citations(docs, [0.33])
        assert "`report.pdf`" in result

    def test_single_source_uses_ticket_id_key(self):
        docs = [FakeDocument(metadata={"ticket_id": "TICKET-42"})]
        result = format_citations(docs, [0.10])
        assert "`TICKET-42`" in result


class TestFormatCitationsDeduplication:

    def test_duplicate_chunks_deduplicated_best_score_kept(self):
        docs = [
            FakeDocument(metadata={"display_name": "faq.md"}, page_content="chunk1"),
            FakeDocument(metadata={"display_name": "faq.md"}, page_content="chunk2"),
        ]
        result = format_citations(docs, [0.90, 0.50])
        assert result.count("`faq.md`") == 1
        assert "(relevance: 0.50)" in result
        assert "(relevance: 0.90)" not in result

    def test_duplicate_keeps_real_score_over_no_score(self):
        docs = [
            FakeDocument(metadata={"display_name": "faq.md"}, page_content="a"),
            FakeDocument(metadata={"display_name": "faq.md"}, page_content="b"),
        ]
        result = format_citations(docs, [-1.0, 0.75])
        assert "(relevance: 0.75)" in result


class TestFormatCitationsScoreHandling:

    def test_score_minus_one_omitted(self):
        docs = [FakeDocument(metadata={"display_name": "readme.md"})]
        result = format_citations(docs, [-1.0])
        assert "relevance" not in result
        assert "`readme.md`" in result

    def test_sorting_lower_is_better(self):
        docs = [
            FakeDocument(metadata={"display_name": "b.md"}),
            FakeDocument(metadata={"display_name": "a.md"}),
        ]
        result = format_citations(docs, [0.90, 0.10])
        pos_a = result.index("`a.md`")
        pos_b = result.index("`b.md`")
        assert pos_a < pos_b

    def test_no_score_entries_sorted_after_scored(self):
        docs = [
            FakeDocument(metadata={"display_name": "noscore.md"}),
            FakeDocument(metadata={"display_name": "scored.md"}),
        ]
        result = format_citations(docs, [-1.0, 0.50])
        pos_scored = result.index("`scored.md`")
        pos_noscore = result.index("`noscore.md`")
        assert pos_scored < pos_noscore


class TestFormatCitationsCollectionLabels:

    def test_multi_collection_shows_labels(self):
        docs = [
            FakeDocument(
                metadata={"display_name": "failover.md", "collection": "runbooks"}
            ),
            FakeDocument(metadata={"display_name": "setup.md", "collection": "guides"}),
        ]
        result = format_citations(docs, [0.92, 0.87])
        assert "[runbooks]" in result
        assert "[guides]" in result

    def test_single_collection_omits_labels(self):
        docs = [
            FakeDocument(metadata={"display_name": "a.md", "collection": "docs"}),
            FakeDocument(metadata={"display_name": "b.md", "collection": "docs"}),
        ]
        result = format_citations(docs, [0.50, 0.60])
        assert "[docs]" not in result

    def test_collection_name_key_also_works(self):
        docs = [
            FakeDocument(metadata={"display_name": "x.md", "collection_name": "alpha"}),
            FakeDocument(metadata={"display_name": "y.md", "collection_name": "beta"}),
        ]
        result = format_citations(docs, [0.10, 0.20])
        assert "[alpha]" in result
        assert "[beta]" in result


class TestFormatCitationsMissingMetadata:

    def test_document_with_none_metadata(self):
        doc = FakeDocument()
        doc.metadata = None
        result = format_citations([doc], [0.5])
        assert result == ""

    def test_document_without_metadata_attr(self):
        class BareDoc:
            page_content = "text"

        result = format_citations([BareDoc()], [0.5])
        assert result == ""

    def test_scores_shorter_than_documents(self):
        docs = [
            FakeDocument(metadata={"display_name": "a.md"}),
            FakeDocument(metadata={"display_name": "b.md"}),
        ]
        result = format_citations(docs, [0.5])
        assert "`a.md`" in result
        assert "`b.md`" in result
        assert result.count("relevance") == 1
