"""
Citation formatter utility for archi.

Provides a pure function to format source documents into markdown citation
blocks. Used by both the native chat UI and the OpenAI-compatible /v1 endpoint.
"""

from typing import List


def format_citations(source_documents: List, scores: List) -> str:
    """
    Format source documents and scores into a markdown citations block.

    Args:
        source_documents: LangChain-style Document objects with .metadata dict
            and .page_content attributes.
        scores: Corresponding relevance scores (lower = more relevant, as they
            represent distances). A score of -1.0 means "no score".

    Returns:
        A markdown string with deduplicated, sorted source citations, or an
        empty string if there are no source documents.
    """
    if not source_documents:
        return ""

    padded_scores = list(scores) if scores else []
    while len(padded_scores) < len(source_documents):
        padded_scores.append(-1.0)

    # Deduplicate by display name, keeping the best (lowest) score
    best_by_name = {}
    for doc, score in zip(source_documents, padded_scores):
        metadata = getattr(doc, "metadata", None) or {}
        display_name = _get_display_name(metadata)
        if not display_name:
            continue

        collection = _get_collection(metadata)

        if display_name not in best_by_name:
            best_by_name[display_name] = {
                "score": score,
                "collection": collection,
            }
        else:
            existing_score = best_by_name[display_name]["score"]
            if score != -1.0 and (existing_score == -1.0 or score < existing_score):
                best_by_name[display_name]["score"] = score

    if not best_by_name:
        return ""

    # Show collection labels only when sources span multiple collections
    collections = {
        entry["collection"]
        for entry in best_by_name.values()
        if entry["collection"]
    }
    show_collection = len(collections) > 1

    # Sort: real scores first (ascending — lower is better), then no-score entries
    entries = sorted(
        best_by_name.items(),
        key=lambda item: (
            0 if item[1]["score"] != -1.0 else 1,
            item[1]["score"] if item[1]["score"] != -1.0 else 0,
        ),
    )

    lines = []
    for name, info in entries:
        parts = [f"- `{name}`"]
        if show_collection and info["collection"]:
            parts.append(f" [{info['collection']}]")
        if info["score"] != -1.0:
            parts.append(f" (relevance: {info['score']:.2f})")
        lines.append("".join(parts))

    return "\n\n---\n**Sources:**\n" + "\n".join(lines)


def _get_display_name(metadata: dict) -> str:
    """Extract a display name from document metadata, trying several keys."""
    for key in ("display_name", "source", "filename", "ticket_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _get_collection(metadata: dict) -> str:
    """Extract collection name from document metadata."""
    for key in ("collection", "collection_name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
