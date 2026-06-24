"""Unit tests for hierarchical parent-child node parsing.

Exercises ``build_hierarchical_nodes``: the structure-aware splitter that
converts a LangChain ``Document`` into parent context nodes plus embedded child
leaf texts (task 2.1 of add-hierarchical-rerank-retrieval).
"""

import pytest
from langchain_core.documents import Document

from src.data_manager.vectorstore.node_parsing import (
    CHILD_EMBEDDING_DIM,
    MARKDOWN_STRATEGY,
    SENTENCE_STRATEGY,
    HierarchicalNode,
    build_hierarchical_nodes,
    embed_child_nodes,
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


class _FakeEmbedder:
    """Stand-in for archi's LangChain ``Embeddings`` model.

    Records the texts passed to ``embed_documents`` and returns vectors of a
    fixed dimension so tests can force a dimension mismatch.
    """

    def __init__(self, dim: int = CHILD_EMBEDDING_DIM):
        self.dim = dim
        self.calls: list = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [[0.0] * self.dim for _ in texts]


def test_embed_child_nodes_uses_archi_embedder():
    """Children are embedded via the provided model's ``embed_documents``."""
    embedder = _FakeEmbedder()
    children = ["child one.", "child two.", "child three."]

    embeddings = embed_child_nodes(embedder, children)

    # The configured archi embedder was used, with exactly the child texts.
    assert embedder.calls == [children]
    assert len(embeddings) == len(children)
    assert all(len(vec) == CHILD_EMBEDDING_DIM for vec in embeddings)


def test_embed_child_nodes_empty_returns_empty_without_calling_model():
    embedder = _FakeEmbedder()
    assert embed_child_nodes(embedder, []) == []
    assert embedder.calls == []


def test_embed_child_nodes_raises_on_dimension_mismatch():
    """A wrong-dimension embedding fails loudly rather than being stored."""
    embedder = _FakeEmbedder(dim=CHILD_EMBEDDING_DIM + 1)
    with pytest.raises(ValueError, match="expected 384"):
        embed_child_nodes(embedder, ["a child sentence."])


def test_embed_child_nodes_accepts_configured_non_minilm_dim():
    """A 1536-dim backend passes when ``expected_dim`` matches the config."""
    embedder = _FakeEmbedder(dim=1536)
    embeddings = embed_child_nodes(embedder, ["a child."], expected_dim=1536)
    assert [len(vec) for vec in embeddings] == [1536]


def test_embed_child_nodes_raises_when_dim_differs_from_configured():
    """A vector that differs from the configured dimension fails loudly."""
    embedder = _FakeEmbedder(dim=CHILD_EMBEDDING_DIM)
    with pytest.raises(ValueError, match="expected 1536"):
        embed_child_nodes(embedder, ["a child."], expected_dim=1536)


def test_embed_child_nodes_raises_on_count_mismatch():
    """One vector per child is required; a short result fails loudly."""

    class _ShortEmbedder:
        def embed_documents(self, texts):
            return [[0.0] * CHILD_EMBEDDING_DIM]  # one vector regardless of input

    with pytest.raises(ValueError, match="expected exactly one embedding per child"):
        embed_child_nodes(_ShortEmbedder(), ["first.", "second."])
