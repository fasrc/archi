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
  header-delimited sections. Every parent and child carries the section's
  ancestor header path in ``metadata["header_path"]`` (``"/"`` for preamble
  and top-level sections — the key is always present under this strategy). A
  section longer than ``parent_chunk_size`` is sub-split into several parents
  (zero overlap); children come from :class:`SentenceSplitter` per parent.
  The strategy is meant for Markdown files only: callers resolve a per-file
  strategy with :func:`resolve_effective_strategy`, which falls back to
  ``"sentence"`` for non-Markdown files. ``MarkdownElementNodeParser`` is
  intentionally not used: it requires an LLM (for table summarisation) which
  the CPU-only ingestion path does not provide. See ``docs/decisions/``.

The ``"character"`` strategy is the legacy ``CharacterTextSplitter`` path and is
handled by the caller, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from llama_index.core import Document as LlamaDocument
from llama_index.core.node_parser import (
    HierarchicalNodeParser,
    MarkdownNodeParser,
    SentenceSplitter,
    get_leaf_nodes,
)
from llama_index.core.schema import NodeRelationship
from llama_index.core.utils import get_tokenizer

# Default parent/child chunk sizes (in tokens, per LlamaIndex's splitters).
# Parents hold a larger context window; children are small, precise leaves.
DEFAULT_PARENT_CHUNK_SIZE = 2048
DEFAULT_CHILD_CHUNK_SIZE = 512

# Explicit child-splitter overlap (tokens), matching HierarchicalNodeParser's
# default on the sentence path. SentenceSplitter's own default is 200 and it
# raises when overlap >= chunk_size, so a small configured child_chunk_size
# would otherwise fail every document at ingest.
CHILD_CHUNK_OVERLAP = 20

SENTENCE_STRATEGY = "sentence"
MARKDOWN_STRATEGY = "markdown"

# Suffixes that count as Markdown for per-file strategy dispatch, compared
# after a lstrip(".").lower() normalization: converted web pages and
# git-source files record "md" (bare), local files record ".md" (dotted,
# raw Path.suffix), and the git source's suffix list is config-driven so
# ".markdown" files are possible.
_MARKDOWN_SUFFIXES = frozenset({"md", "markdown"})

# Default dimension of archi's stock embedder (``sentence-transformers/
# all-MiniLM-L6-v2``). Used only as a fallback when no configured
# ``embedding_dimensions`` is supplied to :func:`embed_child_nodes`; the guard
# itself follows the deployment's configured dimension so non-MiniLM backends
# (e.g. 1536-dim OpenAI) ingest correctly. Child vectors whose dimension does
# not match the ``document_chunks.embedding`` column must never reach the
# database.
CHILD_EMBEDDING_DIM = 384


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
        parents = _parse_markdown(li_document, parent_chunk_size, child_chunk_size)
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
            metadata={**metadata, **extra_metadata},
        )
        for index, (parent_text, child_texts, extra_metadata) in enumerate(parents)
    ]


def resolve_effective_strategy(
    strategy: str,
    *,
    filename: Optional[str] = None,
    suffix: Optional[str] = None,
) -> str:
    """Resolve the strategy to apply to one file.

    The ``markdown`` strategy is meant for Markdown files only: a Markdown
    file keeps it, every other file falls back to ``sentence``. Any other
    configured strategy passes through unchanged. The recorded catalog
    ``suffix`` is authoritative when present; otherwise the ``filename``
    extension decides. Both are normalized (leading dot stripped, lowercased)
    before the comparison against :data:`_MARKDOWN_SUFFIXES`.
    """
    if strategy != MARKDOWN_STRATEGY:
        return strategy
    candidate = suffix if suffix else _filename_suffix(filename)
    if str(candidate).lstrip(".").lower() in _MARKDOWN_SUFFIXES:
        return MARKDOWN_STRATEGY
    return SENTENCE_STRATEGY


def _filename_suffix(filename: Optional[str]) -> str:
    """Extension of ``filename`` (with its dot), or ``""`` when absent."""
    if not filename:
        return ""
    return Path(str(filename)).suffix


def embed_child_nodes(
    embedding_model,
    child_texts: List[str],
    *,
    expected_dim: int = CHILD_EMBEDDING_DIM,
) -> List[List[float]]:
    """Embed child leaf texts with archi's configured embedding model.

    The hierarchical ingestion path MUST embed children with archi's own
    ``embedding_model`` — the LangChain ``Embeddings`` instance built by
    :class:`~src.data_manager.vectorstore.manager.VectorStoreManager` — and
    never a LlamaIndex default embedder, so child vectors stay consistent with
    the query embeddings and the ``document_chunks.embedding`` column.

    Each returned vector is asserted to have ``expected_dim`` dimensions; a
    mismatch raises :class:`ValueError` (fail loudly) rather than letting a
    wrong-dimension vector reach the database. ``expected_dim`` follows the
    deployment's configured ``embedding_dimensions`` so the guard matches the
    actual ``document_chunks.embedding vector(N)`` column for any backend (e.g.
    1536-dim OpenAI), not only 384-dim MiniLM. It defaults to
    :data:`CHILD_EMBEDDING_DIM` only when no configured dimension is supplied.

    Args:
        embedding_model: archi's embedder, exposing ``embed_documents``.
        child_texts: child leaf texts to embed.
        expected_dim: the configured embedding dimension every child vector must
            match; defaults to :data:`CHILD_EMBEDDING_DIM`.

    Returns:
        One embedding vector per input text, each ``expected_dim``-dimensional.
        An empty input yields an empty list.

    Raises:
        ValueError: if the embedder returns the wrong number of vectors, or any
            vector does not have ``expected_dim`` dimensions.
    """
    texts = list(child_texts)
    if not texts:
        return []

    embeddings = embedding_model.embed_documents(texts)

    if len(embeddings) != len(texts):
        raise ValueError(
            f"Embedder returned {len(embeddings)} vectors for {len(texts)} child "
            "texts; expected exactly one embedding per child."
        )

    for index, embedding in enumerate(embeddings):
        dim = len(embedding)
        if dim != expected_dim:
            raise ValueError(
                f"Child embedding {index} has dimension {dim}, expected "
                f"{expected_dim} to match the document_chunks.embedding "
                "column. Refusing to store a wrong-dimension vector."
            )

    return embeddings


def _clamped_overlap(chunk_size: int) -> int:
    """Return :data:`CHILD_CHUNK_OVERLAP`, clamped to at most ``chunk_size``.

    LlamaIndex splitters raise ``ValueError`` only when ``chunk_overlap``
    exceeds ``chunk_size`` (strictly greater), so the clamp preserves every
    legal value exactly — a ``chunk_size`` of 20 keeps the 20-token overlap it
    always had — and only shrinks the overlap for smaller configured sizes
    instead of failing every document at ingest.
    """
    return min(CHILD_CHUNK_OVERLAP, max(chunk_size, 0))


def _parse_sentence(
    li_document: LlamaDocument,
    parent_chunk_size: int,
    child_chunk_size: int,
) -> List["tuple[str, List[str], Dict]"]:
    """Sentence-aware two-level parse via :class:`HierarchicalNodeParser`.

    Returns ``(parent_text, [child_text, ...], extra_metadata)`` triples
    grouped by each leaf's immediate parent, so every returned parent has at
    least one child. The sentence strategy contributes no extra metadata.
    """
    parser = HierarchicalNodeParser.from_defaults(
        chunk_sizes=[parent_chunk_size, child_chunk_size],
        chunk_overlap=_clamped_overlap(min(parent_chunk_size, child_chunk_size)),
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

    parents: List["tuple[str, List[str], Dict]"] = []
    for parent_id in order:
        parent_node = nodes_by_id.get(parent_id)
        parent_text = (
            parent_node.get_content().strip()
            if parent_node is not None
            else " ".join(grouped[parent_id])
        )
        if not parent_text:
            parent_text = " ".join(grouped[parent_id])
        parents.append((parent_text, grouped[parent_id], {}))
    return parents


def _cap_section(
    section_text: str,
    parent_splitter: SentenceSplitter,
    parent_chunk_size: int,
) -> List[str]:
    """Cap one header section at the parent budget without cutting fences.

    A section that fits yields itself unchanged. An oversized section is
    rebuilt fence-aware: fenced code blocks stay atomic while the prose runs
    between them are sentence-split, and the resulting parts are packed back
    greedily up to ``parent_chunk_size`` tokens. A fenced block larger than
    the budget becomes one oversized parent on its own — a bisected fence is
    worse for retrieval context than an occasional large parent.
    """
    pieces = parent_splitter.split_text(section_text)
    if len(pieces) <= 1:
        return [section_text]

    parts: List[str] = []
    for is_fence, segment in _fence_segments(section_text):
        segment = segment.strip("\n")
        if not segment.strip():
            continue
        if is_fence:
            parts.append(segment)
        else:
            parts.extend(
                piece for piece in parent_splitter.split_text(segment) if piece.strip()
            )

    # Pack the parts back greedily, measuring the exact text a parent would
    # persist, so the bytes between parts count against the cap too. A parent
    # is a verbatim slice of the section at the parts' original offsets: a
    # piece the splitter cut mid-token (a long URL or hash) comes back together
    # with nothing inserted. If a piece cannot be located in the source (a
    # splitter fallback rewrote whitespace), parts are joined with a blank line.
    tokenizer = get_tokenizer()
    spans = _locate_parts(section_text, parts)
    if spans is None:
        render = lambda first, last: "\n\n".join(parts[first : last + 1])
    else:
        render = lambda first, last: section_text[spans[first][0] : spans[last][1]]
    return _pack_parts(len(parts), render, tokenizer, parent_chunk_size)


def _locate_parts(text: str, parts: List[str]) -> Optional[List["tuple[int, int]"]]:
    """Return each part's ``(start, end)`` offsets in ``text``, in order.

    Parts are searched from the previous part's end, so repeated text resolves
    to the right occurrence. Returns ``None`` when a part is not a verbatim
    substring of ``text``.
    """
    spans: List["tuple[int, int]"] = []
    cursor = 0
    for part in parts:
        start = text.find(part, cursor)
        if start < 0:
            return None
        cursor = start + len(part)
        spans.append((start, cursor))
    return spans


def _pack_parts(
    count: int,
    render: Callable[[int, int], str],
    tokenizer: Callable[[str], List],
    parent_chunk_size: int,
) -> List[str]:
    """Greedily pack parts ``0..count-1`` (``count`` >= 1) under the budget.

    ``render(first, last)`` returns the text parts ``first..last`` persist as;
    that text is what gets measured, so separators count. A single part larger
    than the budget becomes a parent on its own.
    """
    capped: List[str] = []
    first = 0
    for index in range(1, count):
        if len(tokenizer(render(first, index))) > parent_chunk_size:
            capped.append(render(first, index - 1))
            first = index
    capped.append(render(first, count - 1))
    return capped


# Fence markers _fence_segments recognises. The upstream MarkdownNodeParser
# tracks backticks only, so a `#` line inside a tilde fence still starts a new
# section upstream; the cap nevertheless keeps either kind of fence whole.
_FENCE_MARKERS = ("```", "~~~")


def _fence_marker(line: str) -> Optional[str]:
    """Return the fence marker ``line`` opens or closes with, if any."""
    stripped = line.lstrip()
    for marker in _FENCE_MARKERS:
        if stripped.startswith(marker):
            return marker
    return None


def _fence_segments(text: str) -> List["tuple[bool, str]"]:
    """Split ``text`` into ordered ``(is_fence, segment)`` runs.

    Extends the upstream ``MarkdownNodeParser`` rule (a line whose lstripped
    form starts with three backticks toggles fence state) to tilde fences, and
    tracks the opening marker so only a line that starts with the same marker
    closes the fence. An unterminated fence extends to the end of the text and
    stays atomic.
    """
    segments: List["tuple[bool, str]"] = []
    buffer: List[str] = []
    open_marker: Optional[str] = None
    for line in text.split("\n"):
        marker = _fence_marker(line)
        if open_marker is None and marker is not None:
            if buffer:
                segments.append((False, "\n".join(buffer)))
            buffer = [line]
            open_marker = marker
            continue
        buffer.append(line)
        if marker is not None and marker == open_marker:
            segments.append((True, "\n".join(buffer)))
            buffer = []
            open_marker = None
    if buffer:
        segments.append((open_marker is not None, "\n".join(buffer)))
    return segments


def _parse_markdown(
    li_document: LlamaDocument,
    parent_chunk_size: int,
    child_chunk_size: int,
) -> List["tuple[str, List[str], Dict]"]:
    """Header-aware parse: sections are parents, sentence-split into children.

    Each :class:`MarkdownNodeParser` section carries its ancestor header path
    in ``metadata["header_path"]`` (``"/"`` for preamble and top-level
    sections); that key is propagated as the section's extra metadata. A
    section longer than ``parent_chunk_size`` is sub-split into several
    parents with ``chunk_overlap=0`` (sections are semantic units, not
    sliding windows), all carrying the same ``header_path``.
    """
    section_parser = MarkdownNodeParser()
    parent_splitter = SentenceSplitter(chunk_size=parent_chunk_size, chunk_overlap=0)
    child_splitter = SentenceSplitter(
        chunk_size=child_chunk_size,
        chunk_overlap=_clamped_overlap(child_chunk_size),
    )
    section_nodes = section_parser.get_nodes_from_documents([li_document])

    parents: List["tuple[str, List[str], Dict]"] = []
    for section in section_nodes:
        section_text = (section.get_content() or "").strip()
        if not section_text:
            continue
        header_path = section.metadata.get("header_path") or "/"
        for parent_text in _cap_section(
            section_text, parent_splitter, parent_chunk_size
        ):
            parent_text = parent_text.strip()
            if not parent_text:
                continue
            child_texts = [
                chunk.strip()
                for chunk in child_splitter.split_text(parent_text)
                if chunk.strip()
            ]
            if not child_texts:
                child_texts = [parent_text]
            parents.append((parent_text, child_texts, {"header_path": header_path}))
    return parents
