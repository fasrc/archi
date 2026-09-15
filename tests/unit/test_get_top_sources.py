"""
Unit tests for ChatWrapper.get_top_sources.

Written against the corrected semantics: retriever scores are cosine similarities
(higher = more relevant).  These tests fail on the pre-fix implementation, which
sorts ascending and treats the threshold as a ceiling rather than a floor.
"""

from dataclasses import dataclass, field
from typing import Dict

from src.interfaces.chat_app.app import ChatWrapper


@dataclass
class _Doc:
    """Minimal stand-in for a LangChain Document."""

    metadata: Dict = field(default_factory=dict)
    page_content: str = ""


def _make_wrapper(similarity_score_reference=0.0):
    """Return a ChatWrapper that only has the attributes get_top_sources touches."""
    wrapper = object.__new__(ChatWrapper)
    wrapper.similarity_score_reference = similarity_score_reference
    wrapper.sources_config = {}
    return wrapper


class TestGetTopSourcesOrdering:

    def test_highest_scoring_doc_leads(self):
        """The document with the highest similarity score should appear first."""
        wrapper = _make_wrapper()
        docs = [
            _Doc(metadata={"display_name": "low.md"}),
            _Doc(metadata={"display_name": "high.md"}),
        ]
        result = wrapper.get_top_sources(docs, [0.3, 0.9])
        assert len(result) == 2
        assert result[0]["display"] == "high.md"
        assert result[1]["display"] == "low.md"

    def test_three_docs_ordered_descending(self):
        """All documents are returned in descending score order."""
        wrapper = _make_wrapper()
        docs = [
            _Doc(metadata={"display_name": "mid.md"}),
            _Doc(metadata={"display_name": "low.md"}),
            _Doc(metadata={"display_name": "high.md"}),
        ]
        result = wrapper.get_top_sources(docs, [0.5, 0.2, 0.9])
        assert [r["display"] for r in result] == ["high.md", "mid.md", "low.md"]

    def test_sentinel_sorts_after_real_scores(self):
        """-1.0 sentinel documents appear after all real-scored documents."""
        wrapper = _make_wrapper()
        docs = [
            _Doc(metadata={"display_name": "sentinel.md"}),
            _Doc(metadata={"display_name": "real.md"}),
        ]
        result = wrapper.get_top_sources(docs, [-1.0, 0.7])
        assert len(result) == 2
        assert result[0]["display"] == "real.md"
        assert result[1]["display"] == "sentinel.md"


class TestGetTopSourcesThreshold:

    def test_below_floor_excluded(self):
        """A document scoring below the operator-set floor is not cited."""
        wrapper = _make_wrapper(similarity_score_reference=0.3)
        docs = [
            _Doc(metadata={"display_name": "above.md"}),
            _Doc(metadata={"display_name": "below.md"}),
        ]
        result = wrapper.get_top_sources(docs, [0.9, 0.1])
        assert len(result) == 1
        assert result[0]["display"] == "above.md"

    def test_below_floor_breaks_iteration(self):
        """Everything after the first below-floor document is also excluded."""
        wrapper = _make_wrapper(similarity_score_reference=0.3)
        docs = [
            _Doc(metadata={"display_name": "high.md"}),
            _Doc(metadata={"display_name": "below.md"}),
            _Doc(metadata={"display_name": "also_high.md"}),
        ]
        # After descending sort: also_high (0.9), high (0.8), below (0.1)
        result = wrapper.get_top_sources(docs, [0.8, 0.1, 0.9])
        display_names = [r["display"] for r in result]
        assert "below.md" not in display_names
        assert len(result) == 2
        assert set(display_names) == {"high.md", "also_high.md"}

    def test_default_floor_zero_includes_all(self):
        """With the shipped default of 0.0, no source is filtered."""
        wrapper = _make_wrapper(similarity_score_reference=0.0)
        docs = [
            _Doc(metadata={"display_name": "a.md"}),
            _Doc(metadata={"display_name": "b.md"}),
            _Doc(metadata={"display_name": "c.md"}),
        ]
        result = wrapper.get_top_sources(docs, [0.1, 0.5, 0.9])
        assert len(result) == 3

    def test_sentinel_not_threshold_filtered(self):
        """-1.0 sentinel bypasses the floor check and appears in results."""
        wrapper = _make_wrapper(similarity_score_reference=0.3)
        docs = [
            _Doc(metadata={"display_name": "real.md"}),
            _Doc(metadata={"display_name": "sentinel.md"}),
        ]
        result = wrapper.get_top_sources(docs, [0.9, -1.0])
        display_names = [r["display"] for r in result]
        assert "sentinel.md" in display_names

    def test_sentinel_not_a_stopping_point(self):
        """-1.0 sentinel does not trigger the break; later docs are still cited."""
        wrapper = _make_wrapper(similarity_score_reference=0.0)
        docs = [
            _Doc(metadata={"display_name": "a.md"}),
            _Doc(metadata={"display_name": "sentinel.md"}),
        ]
        result = wrapper.get_top_sources(docs, [0.9, -1.0])
        assert len(result) == 2
