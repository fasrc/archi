"""Structural parent-child node parsing for hierarchical retrieval.

Converts a LangChain ``Document`` into a LlamaIndex ``Document`` and then into a
set of *parent* context nodes, each holding the smaller *child* leaf texts that
get embedded and stored in ``document_chunks``. Parents carry the larger
surrounding context (stored in ``document_parent_nodes``); children are linked
back to their parent via ``metadata.parent_id`` at persistence time.

Two strategies are supported, mirroring ``data_manager.chunking.strategy``:

* ``"sentence"`` (default) — :class:`HierarchicalNodeParser` built on
  :class:`SentenceSplitter`. Segments on sentence boundaries (never a fixed
  character count) at both the parent and child levels. Suitable for the
  HTML-derived FASRC corpus.
* ``"markdown"`` — :class:`MarkdownNodeParser` carves the document into
  header-delimited sections (parents); each section is then split into children
  with :class:`SentenceSplitter`. ``MarkdownElementNodeParser`` is intentionally
  not used: it requires an LLM (for table summarisation) which the CPU-only
  ingestion path does not provide. See ``docs/decisions/``.

The ``"character"`` strategy is the legacy ``CharacterTextSplitter`` path and is
handled by the caller, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from llama_index.core import Document as LlamaDocument
from llama_index.core.node_parser import (
    HierarchicalNodeParser,
    MarkdownNodeParser,
    SentenceSplitter,
    get_leaf_nodes,
)
from llama_index.core.schema import NodeRelationship

# Default parent/child chunk sizes (in tokens, per LlamaIndex's splitters).
# Parents hold a larger context window; children are small, precise leaves.
DEFAULT_PARENT_CHUNK_SIZE = 2048
DEFAULT_CHILD_CHUNK_SIZE = 512

SENTENCE_STRATEGY = "sentence"
MARKDOWN_STRATEGY = "markdown"


@dataclass
class HierarchicalNode:
    """A parent context node paired with its embedded child leaf texts.

    ``parent_text`` is persisted to ``document_parent_nodes``; every entry in
    ``child_texts`` becomes an embedded row in ``document_chunks`` that
    references this parent. ``metadata`` is the document-level metadata
    propagated to the parent node.
    """

    parent_index: int
    parent_text: str
    child_texts: List[str]
    metadata: Dict = field(default_factory=dict)


def build_hierarchical_nodes(
    document,
    *,
    strategy: str = SENTENCE_STRATEGY,
    parent_chunk_size: int = DEFAULT_PARENT_CHUNK_SIZE,
    child_chunk_size: int = DEFAULT_CHILD_CHUNK_SIZE,
) -> List[HierarchicalNode]:
    """Parse a LangChain ``Document`` into hierarchical parent/child nodes.

    Args:
        document: A LangChain ``Document`` (duck-typed: ``page_content`` and
            ``metadata`` attributes are read).
        strategy: ``"sentence"`` (default) or ``"markdown"``.
        parent_chunk_size: Target size of parent context nodes.
        child_chunk_size: Target size of embedded child leaf nodes.

    Returns:
        A list of :class:`HierarchicalNode`. Each parent has at least one child;
        a document with no usable text yields an empty list.
    """
    text = getattr(document, "page_content", "") or ""
    if not text.strip():
        return []

    metadata = dict(getattr(document, "metadata", {}) or {})
    li_document = LlamaDocument(text=text, metadata=metadata)

    if strategy == MARKDOWN_STRATEGY:
        parents = _parse_markdown(li_document, child_chunk_size)
    elif strategy == SENTENCE_STRATEGY:
        parents = _parse_sentence(li_document, parent_chunk_size, child_chunk_size)
    else:
        raise ValueError(
            f"Unsupported hierarchical chunking strategy: {strategy!r}. "
            f"Expected {SENTENCE_STRATEGY!r} or {MARKDOWN_STRATEGY!r}."
        )

    return [
        HierarchicalNode(
            parent_index=index,
            parent_text=parent_text,
            child_texts=child_texts,
            metadata=dict(metadata),
        )
        for index, (parent_text, child_texts) in enumerate(parents)
    ]


def _parse_sentence(
    li_document: LlamaDocument,
    parent_chunk_size: int,
    child_chunk_size: int,
) -> List["tuple[str, List[str]]"]:
    """Sentence-aware two-level parse via :class:`HierarchicalNodeParser`.

    Returns ``(parent_text, [child_text, ...])`` pairs grouped by each leaf's
    immediate parent, so every returned parent has at least one child.
    """
    parser = HierarchicalNodeParser.from_defaults(
        chunk_sizes=[parent_chunk_size, child_chunk_size]
    )
    nodes = parser.get_nodes_from_documents([li_document])
    nodes_by_id = {node.node_id: node for node in nodes}
    leaves = get_leaf_nodes(nodes)

    # Group leaves by their immediate parent node id, preserving first-seen order.
    grouped: "dict[str, List[str]]" = {}
    order: List[str] = []
    for leaf in leaves:
        child_text = (leaf.get_content() or "").strip()
        if not child_text:
            continue
        parent_rel = leaf.relationships.get(NodeRelationship.PARENT)
        parent_id = parent_rel.node_id if parent_rel is not None else leaf.node_id
        if parent_id not in grouped:
            grouped[parent_id] = []
            order.append(parent_id)
        grouped[parent_id].append(child_text)

    parents: List["tuple[str, List[str]]"] = []
    for parent_id in order:
        parent_node = nodes_by_id.get(parent_id)
        parent_text = (
            parent_node.get_content().strip()
            if parent_node is not None
            else " ".join(grouped[parent_id])
        )
        if not parent_text:
            parent_text = " ".join(grouped[parent_id])
        parents.append((parent_text, grouped[parent_id]))
    return parents


def _parse_markdown(
    li_document: LlamaDocument,
    child_chunk_size: int,
) -> List["tuple[str, List[str]]"]:
    """Header-aware parse: sections are parents, sentence-split into children."""
    section_parser = MarkdownNodeParser()
    child_splitter = SentenceSplitter(chunk_size=child_chunk_size)
    section_nodes = section_parser.get_nodes_from_documents([li_document])

    parents: List["tuple[str, List[str]]"] = []
    for section in section_nodes:
        parent_text = (section.get_content() or "").strip()
        if not parent_text:
            continue
        child_texts = [
            chunk.strip()
            for chunk in child_splitter.split_text(parent_text)
            if chunk.strip()
        ]
        if not child_texts:
            child_texts = [parent_text]
        parents.append((parent_text, child_texts))
    return parents
