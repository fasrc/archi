"""Unit tests for hierarchical parent-child node parsing.

Exercises ``build_hierarchical_nodes``: the structure-aware splitter that
converts a LangChain ``Document`` into parent context nodes plus embedded child
leaf texts (task 2.1 of add-hierarchical-rerank-retrieval).
"""

import pytest
from langchain_core.documents import Document

from src.data_manager.vectorstore.node_parsing import (
    MARKDOWN_STRATEGY,
    SENTENCE_STRATEGY,
    HierarchicalNode,
    build_hierarchical_nodes,
)


def _sentences(n: int) -> str:
    return " ".join(
        f"This is sentence number {i} with some filler words." for i in range(n)
    )


def test_sentence_strategy_produces_parents_with_children():
    """Each parent node has at least one child and the document metadata."""
    doc = Document(page_content=_sentences(120), metadata={"source": "doc.txt"})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=SENTENCE_STRATEGY,
        parent_chunk_size=256,
        child_chunk_size=64,
    )

    assert nodes, "expected at least one parent node"
    assert all(isinstance(node, HierarchicalNode) for node in nodes)
    for node in nodes:
        # Spec: one or more child nodes per parent.
        assert len(node.child_texts) >= 1
        assert node.parent_text.strip()
        assert node.metadata == {"source": "doc.txt"}
    # parent_index is a stable 0-based enumeration.
    assert [node.parent_index for node in nodes] == list(range(len(nodes)))


def test_sentence_children_are_contained_in_their_parent():
    """Children are sentence-segmented pieces of their parent's context."""
    doc = Document(page_content=_sentences(120), metadata={})

    nodes = build_hierarchical_nodes(
        doc,
        strategy=SENTENCE_STRATEGY,
        parent_chunk_size=256,
        child_chunk_size=64,
    )

    # A multi-sentence corpus should split into more than one parent at this size.
    assert len(nodes) >= 2
    for node in nodes:
        for child in node.child_texts:
            # Child text is segmented on sentence boundaries: a child never ends
            # mid-sentence relative to the source (no fixed-character cut marks).
            assert child.strip() == child
            assert child in node.parent_text


def test_markdown_strategy_uses_header_sections_as_parents():
    """Markdown sections become parents, each split into sentence children."""
    md = (
        "# Title\n\n"
        "Intro paragraph one. Intro paragraph two with more text.\n\n"
        "## Section A\n\n"
        "Section A body sentence one. Section A body sentence two.\n\n"
        "## Section B\n\n"
        "Section B body content here. Another sentence in section B.\n"
    )
    doc = Document(page_content=md, metadata={"source": "guide.md"})

    nodes = build_hierarchical_nodes(doc, strategy=MARKDOWN_STRATEGY)

    # Three header-delimited sections -> three parents.
    assert len(nodes) == 3
    assert any("Section A" in node.parent_text for node in nodes)
    assert any("Section B" in node.parent_text for node in nodes)
    for node in nodes:
        assert len(node.child_texts) >= 1
        assert node.metadata == {"source": "guide.md"}


def test_empty_document_yields_no_nodes():
    assert build_hierarchical_nodes(Document(page_content="   ", metadata={})) == []


def test_missing_page_content_yields_no_nodes():
    class _Bare:
        metadata = {"source": "x"}

    assert build_hierarchical_nodes(_Bare()) == []


def test_unsupported_strategy_raises():
    doc = Document(page_content="some text here.", metadata={})
    with pytest.raises(ValueError, match="Unsupported hierarchical chunking strategy"):
        build_hierarchical_nodes(doc, strategy="character")
