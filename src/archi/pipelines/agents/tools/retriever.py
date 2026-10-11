from __future__ import annotations

import math
from typing import Callable, Iterable, Optional, Sequence, Tuple

from langchain.tools import tool
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from src.archi.pipelines.agents.tools.base import require_tool_permission
from src.archi.pipelines.agents.tools.result_limits import clamp_result
from src.utils.logging import get_logger

logger = get_logger(__name__)


# Metadata key the hierarchical retriever records its cross-encoder score under
# (``hierarchical_retriever.py``). A retriever that returns bare ``Document``
# objects has nowhere else to put a score: ``BaseRetriever.invoke()`` is
# contractually ``List[Document]``, so it cannot hand back tuples the way
# ``HybridRetriever`` does.
_METADATA_SCORE_KEY = "rerank_score"

# Metadata keys the hierarchical retriever records the best-ranked child's span
# under (``hierarchical_retriever.py``): the passage a parent was returned for.
_MATCHED_START_KEY = "matched_start"
_MATCHED_END_KEY = "matched_end"

_SNIPPET_SEPARATOR = "\n\n"
_ELLIPSIS = "..."
# Smallest body a fair share may shrink to. Below this a snippet stops being
# evidence; pathological headers are left to the whole-result clamp instead.
_MIN_BODY_CHARS = 200


def _matched_text(doc: Document) -> str:
    """Return the span of ``page_content`` the document was ranked for, if valid.

    The span is used only when both offsets are integers (``bool`` excluded, as in
    ``_coerce_score``) with ``0 <= start < end <= len(page_content)``; otherwise
    the whole ``page_content`` is returned, as for a retriever that records none.
    """
    text = doc.page_content
    start = doc.metadata.get(_MATCHED_START_KEY)
    end = doc.metadata.get(_MATCHED_END_KEY)
    if (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and 0 <= start < end <= len(text)
    ):
        return text[start:end]
    return text


def _coerce_score(value: object) -> Optional[float]:
    """Return ``value`` as a float, or None if it cannot be rendered as one.

    ``bool`` is excluded deliberately: it subclasses ``int``, so True would
    otherwise render as ``Score: 1.0000``. Non-finite floats are excluded too,
    so a NaN never reaches the model as if it were a relevance score.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score if math.isfinite(score) else None


def _normalize_results(
    results: Iterable[object],
) -> Sequence[Tuple[Document, Optional[float]]]:
    """Coerce retriever outputs into (Document, score) tuples.

    A retriever states its score either by returning a ``(Document, score)``
    tuple or by recording it on document metadata. An explicit tuple score wins
    over a metadata one; the metadata score is read only when the tuple states
    nothing, so the default hierarchical-rerank path stops rendering
    ``Score: n/a`` for every document (issue #464).
    """
    normalized: list[Tuple[Document, Optional[float]]] = []
    doc: Document
    stated: Optional[float]
    for item in results:
        if isinstance(item, Document):
            doc, stated = item, None
        elif (
            isinstance(item, tuple) and len(item) >= 2 and isinstance(item[0], Document)
        ):
            doc, stated = item[0], _coerce_score(item[1])
        else:
            continue
        if stated is None:
            stated = _coerce_score(doc.metadata.get(_METADATA_SCORE_KEY))
        normalized.append((doc, stated))
    return normalized


def _format_documents_for_llm(
    docs: Sequence[Tuple[Document, Optional[float]]],
    *,
    max_documents: int,
    max_chars: int,
    max_result_chars: Optional[int] = None,
) -> str:
    """Render retrieved documents into a compact string.

    Each body is the document's matched span (``_matched_text``), truncated to
    ``max_chars``. Given ``max_result_chars``, the limit also shrinks to each
    document's fair share of that ceiling after the headers, separators and
    ``...`` markers (floored at ``_MIN_BODY_CHARS``), so every rendered document
    fits rather than the whole-result clamp cutting the last ones.
    """
    if not docs:
        return "No documents found in the knowledge base for this query."

    heads = []
    bodies = []
    for idx, (doc, score) in enumerate(docs[:max_documents], start=1):
        filename = doc.metadata.get("filename") or "unknown source"
        # Human-readable citation text the model can hyperlink: prefer the clean
        # title, fall back to the URL-slug display_name, then the filename. Never
        # the resource hash (that is a content fingerprint, not a label).
        title = (
            doc.metadata.get("title") or doc.metadata.get("display_name") or filename
        )
        url = doc.metadata.get("url")
        hash = doc.metadata.get("resource_hash") or "n/a"
        # Surface title + url so the agent can cite inline as [title](url); keep
        # the hash for the fetch_catalog_document companion tool.
        url_part = f" <{url}>" if url else ""
        header = f"[{idx}] {title}{url_part} (hash={hash})"
        footer = (
            f"Score: {score:.4f}" if isinstance(score, (float, int)) else "Score: n/a"
        )
        heads.append(f"{header}\n{footer}\n")
        bodies.append(_matched_text(doc).strip())

    limit = max_chars
    if max_result_chars is not None and heads:
        overhead = (
            sum(len(head) for head in heads)
            + len(_SNIPPET_SEPARATOR) * (len(heads) - 1)
            + len(_ELLIPSIS) * len(heads)
        )
        share = (max_result_chars - overhead) // len(heads)
        limit = min(max_chars, max(_MIN_BODY_CHARS, share))

    snippets = []
    for head, text in zip(heads, bodies):
        if len(text) > limit:
            text = f"{text[:limit].rstrip()}{_ELLIPSIS}"
        snippets.append(f"{head}{text}")

    return _SNIPPET_SEPARATOR.join(snippets)


# Enforced ceiling on the complete serialized retrieval result (issue #235).
# ``max_chars`` bounds ``doc.page_content`` only; the snippet header interpolates
# ``title``/``url``/``resource_hash`` straight from document metadata with no cap,
# so one document with pathological metadata can produce an arbitrarily large
# result. A default result is every retrieved document (up to 8), each body capped
# at 1200 chars or its fair share of this ceiling: 5 documents render at the full
# 1200 (~6.7k chars), 8 at ~850 each.
DEFAULT_RETRIEVER_RESULT_CHARS = 8000


def create_retriever_tool(
    retriever: BaseRetriever,
    *,
    name: str = "search_knowledge_base",
    description: Optional[str] = None,
    max_documents: int = 8,
    max_chars: int = 1200,
    max_result_chars: int = DEFAULT_RETRIEVER_RESULT_CHARS,
    store_docs: Optional[Callable[[str, Sequence[Document]], None]] = None,
    required_permission: Optional[str] = None,
    store_tool_input: Optional[Callable[[str, object], None]] = None,
    enforce_budget: Optional[Callable[[], Optional[str]]] = None,
) -> Callable[[str], str]:
    """
    Wrap a `BaseRetriever` instance in a LangChain tool.

    The resulting tool returns a formatted string combining the retrieved documents
    so the calling agent can ground its responses in the vector store content.
    If ``store_docs`` is provided, it will be invoked with the tool name and
    the list of retrieved ``Document`` objects before formatting the response.

    Args:
        retriever: The LangChain retriever instance to wrap.
        name: The name of the tool.
        description: Human-readable description of the tool.
        max_documents: Maximum number of documents to render. The default covers
            every document the default retrievers return, so the model reads each
            page the user is shown as a source.
        max_chars: Maximum characters per document body. A body is the passage
            the document was ranked for when the retriever records one.
        max_result_chars: Enforced ceiling on the *complete serialized output*
            (issue #235). ``max_chars`` bounds page content only, leaving the
            metadata-derived header uncapped. Each body is also limited to its
            fair share of this ceiling, so all rendered documents fit.
        store_docs: Optional callback to store retrieved documents.
        required_permission: Optional RBAC permission required to use this tool.
            If None, no permission check is performed (allow all).
        store_tool_input: Optional callback invoked with (tool_name, {"query": query})
            to record the runtime input before retrieval (used to backfill streamed
            tool-call args).
        enforce_budget: Optional zero-arg callback checked at the start of every
            call. If it returns a non-empty string, the tool short-circuits and
            returns that string instead of retrieving (used to cap per-turn calls);
            returning None allows the call to proceed.
    """

    tool_description = description or (
        "Search the indexed knowledge base for relevant passages.\n"
        "Input: query string.\n"
        "Output: ranked snippets with source filename, resource hash, and score.\n"
        'Example input: "transfer errors in CMS".'
    )

    @tool(name, description=tool_description)
    @require_tool_permission(required_permission)
    def _retriever_tool(query: str) -> str:
        if enforce_budget is not None:
            budget_msg = enforce_budget()
            if budget_msg is not None:
                logger.info(
                    "Retriever tool '%s' over budget; returning synthetic response",
                    name,
                )
                return budget_msg
        logger.debug("Retriever tool '%s' called with query=%r", name, query)
        if store_tool_input:
            try:
                store_tool_input(name, {"query": query})
            except Exception:
                logger.debug(
                    "Failed to store runtime input for tool '%s'", name, exc_info=True
                )
        if query is None or not str(query).strip():
            logger.warning("Retriever tool '%s' received empty query", name)
        results = retriever.invoke(query)
        docs = _normalize_results(results or [])
        if store_docs:
            store_docs(f"{name}: {query}", [doc for doc, _ in docs])
        rendered = _format_documents_for_llm(
            docs,
            max_documents=max_documents,
            max_chars=max_chars,
            max_result_chars=max_result_chars,
        )
        return clamp_result(rendered, max_result_chars)

    return _retriever_tool
