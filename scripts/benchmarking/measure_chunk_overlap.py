#!/usr/bin/env python
"""Measure the overlap the ingest's chunking ACTUALLY carries across chunk boundaries.

``chunk_overlap`` is a *budget*, not a guarantee. ``SentenceSplitter`` copies back
only whole sentences that fit inside it, so a budget smaller than a typical
sentence (15-30 tokens on the FASRC corpus) frequently carries nothing at all.
The configured number therefore says very little about the continuity a chunking
setting really provides — you have to measure it on real text.

This script does that: for each candidate overlap it reports how many boundaries
carry nothing, how many tokens the typical boundary actually repeats, and what
the setting costs in duplicated embedded tokens (index inflation).

Written for the v2026.10.0 feature-matrix campaign (#396) and the configurable
`chunking.chunk_overlap` key (#403), so a value gets picked from numbers rather
than from convention.

What is measured — the production chunking, not a stand-in. Each document runs
through the same two-level ``HierarchicalNodeParser`` the ingest's ``sentence``
strategy builds (``src/data_manager/vectorstore/node_parsing.py``,
``_parse_sentence``): parents of ``--parent-chunk-size`` tokens, children of
``--chunk-size`` tokens, one overlap budget applied at both levels, clamped to
the smaller size exactly like ``_clamped_overlap``. The boundaries counted are
those between consecutive *child* chunks of one document — the rows that get
embedded — including the boundary where one parent ends and the next begins.

How the carried text is found — from source offsets, not string inference. The
splitter emits every chunk as a verbatim substring of its document, so each
chunk is located by a forward search from just after the previous chunk's
start, and the text carried across a boundary is exactly
``previous.end - following.start`` characters. This matters: a scraped page
often repeats a block verbatim (a navigation menu rendered twice, a page stored
several times), and a plain suffix/prefix string match then reports a long
"overlap" the splitter never copied. The search rejects any occurrence that
would imply more carried tokens than the budget allows, which is why
``chunk_overlap=0`` stays in the default sweep as the control row: it must read
0 at every boundary.

The whitespace-normalized, character-level string matcher remains as the
fallback for the rare chunk the splitter did not emit verbatim (when it must
cut a sentence longer than the chunk budget, its regex pass drops repeated
punctuation). Characters rather than token ids, because a boundary that falls
mid-token — ``https://github.`` | ``com/fasrc/...`` — re-tokenizes differently
on each side and a token-sequence comparison would report zero. Boundaries that
needed the fallback are counted per row (``fallback_boundaries``).

Corpus source: the ``documents`` table stores no content, so dump the
materialized parents re-joined per document. They differ from the source text
only by what the ingest's own 20-token parent budget copied (mostly nothing).
Join to the chunks that reference them: ``document_parent_nodes`` keeps the
parents of every past ingest run, so an unjoined dump repeats each document
once per run. Use ``-0`` so each document ends with a NUL byte and is chunked
on its own; a dump without it is treated as one document and a few boundaries
then straddle documents.

Usage:
    docker exec postgres-dev psql -U archi -d archi-db -t -A -0 -c \\
      "WITH live AS (SELECT DISTINCT (metadata->>'parent_id')::int AS parent_id
                     FROM document_chunks WHERE metadata ? 'parent_id')
       SELECT string_agg(p.parent_text, E'\\n\\n' ORDER BY p.parent_index)
       FROM document_parent_nodes p JOIN live ON live.parent_id = p.id
       GROUP BY p.document_id ORDER BY p.document_id;" > corpus.txt

    python scripts/benchmarking/measure_chunk_overlap.py corpus.txt
    python scripts/benchmarking/measure_chunk_overlap.py corpus.txt \\
      --chunk-size 512 --parent-chunk-size 2048 \\
      --overlap 20 --overlap 64 --overlap 128 --json out.json

Exit code 0 on success; 1 when no usable text was supplied.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

_WHITESPACE = re.compile(r"\s+")

# NUL cannot appear in corpus text (PostgreSQL rejects it, and the ingest strips
# it at manager.py), so it serves two jobs: the record separator between
# documents in a ``psql -0`` dump, and the sentinel of the fallback string
# matcher, which it can therefore never be matched across.
DOCUMENT_SEPARATOR = "\x00"
_SEPARATOR = DOCUMENT_SEPARATOR

# Mirror DEFAULT_PARENT_CHUNK_SIZE / DEFAULT_CHILD_CHUNK_SIZE in
# src/data_manager/vectorstore/node_parsing.py; a unit test pins them equal.
DEFAULT_PARENT_CHUNK_SIZE = 2048
DEFAULT_CHUNK_SIZE = 512
# 0 is the control row: the offsets must read nothing carried at every boundary.
DEFAULT_OVERLAPS = (0, 20, 64, 128)


@dataclass(frozen=True)
class Chunk:
    """One embedded child chunk and its character span in its document.

    ``start``/``end`` are ``None`` when the chunk was not found verbatim in its
    document (see :func:`place_chunks`).
    """

    text: str
    document: int
    start: Optional[int]
    end: Optional[int]


def normalize_whitespace(text: str) -> str:
    """Collapse every whitespace run to a single space and strip the ends.

    Chunkers re-join text with their own spacing, so raw whitespace differs on
    the two sides of a boundary even when the words repeat exactly.
    """
    return _WHITESPACE.sub(" ", text).strip()


def longest_overlap_chars(a: str, b: str) -> int:
    """Length of the longest suffix of ``a`` that is also a prefix of ``b``.

    Both sides are whitespace-normalized first. Uses the KMP prefix function
    over ``b + SEP + a`` so the answer is exact in linear time; the sentinel
    stops a match from running through the join.
    """
    a_norm = normalize_whitespace(a)
    b_norm = normalize_whitespace(b)
    if not a_norm or not b_norm:
        return 0

    combined = b_norm + _SEPARATOR + a_norm
    failure = [0] * len(combined)
    candidate = 0
    for index in range(1, len(combined)):
        while candidate and combined[index] != combined[candidate]:
            candidate = failure[candidate - 1]
        if combined[index] == combined[candidate]:
            candidate += 1
        failure[index] = candidate
    return failure[-1]


def overlap_text(a: str, b: str) -> str:
    """The shared span between the end of ``a`` and the start of ``b``."""
    shared = longest_overlap_chars(a, b)
    return normalize_whitespace(b)[:shared] if shared else ""


def split_records(text: str) -> List[str]:
    """Split a ``psql -0`` dump into documents; text without NUL is one document."""
    return [record for record in text.split(DOCUMENT_SEPARATOR) if record.strip()]


def place_chunks(
    text: str,
    chunk_texts: Sequence[str],
    *,
    document: int,
    budget: int,
    tokenizer,
) -> List[Chunk]:
    """Locate each emitted chunk in its document, in order.

    The splitter emits chunks as verbatim substrings whose ends never move
    backwards (a child made only of text copied from the previous parent ends
    exactly where the previous chunk ends), so each chunk is the first
    occurrence that ends no earlier than the previous chunk — except where a
    page repeats a block longer than a chunk: that occurrence can then sit
    inside the previous chunk and imply an overlap the splitter never copied.
    An occurrence implying more than ``budget`` carried tokens is skipped in
    favour of a later one; when none fits (tokenizing the joined overlap can
    count differently from the splitter's per-sentence sizes) the earliest
    verbatim occurrence is kept. A chunk not found verbatim gets no offsets.
    """
    chunks: List[Chunk] = []
    previous_end: Optional[int] = None
    for chunk_text in chunk_texts:
        cursor = 0
        if previous_end is not None:
            cursor = max(0, previous_end - len(chunk_text))
        earliest = start = text.find(chunk_text, cursor)
        while (
            start >= 0
            and previous_end is not None
            and start < previous_end
            and len(tokenizer(chunk_text[: previous_end - start])) > budget
        ):
            start = text.find(chunk_text, start + 1)
        if start < 0:
            start = earliest
        if start < 0:
            chunks.append(
                Chunk(text=chunk_text, document=document, start=None, end=None)
            )
            continue
        end = start + len(chunk_text)
        chunks.append(Chunk(text=chunk_text, document=document, start=start, end=end))
        previous_end = end
    return chunks


def split_documents(
    documents: Sequence[str],
    *,
    chunk_size: int,
    parent_chunk_size: int,
    overlap: int,
) -> List[Chunk]:
    """Chunk each document the way the ingest's ``sentence`` strategy does.

    Mirrors ``_parse_sentence`` in node_parsing.py: a two-level
    ``HierarchicalNodeParser`` with one overlap budget at both levels, clamped
    to the smaller chunk size. Returns the leaf (child) chunks in emission
    order, each placed in its document by :func:`place_chunks`.
    """
    from llama_index.core import Document
    from llama_index.core.node_parser import HierarchicalNodeParser, get_leaf_nodes
    from llama_index.core.utils import get_tokenizer

    budget = max(0, min(overlap, chunk_size, parent_chunk_size))
    parser = HierarchicalNodeParser.from_defaults(
        chunk_sizes=[parent_chunk_size, chunk_size], chunk_overlap=budget
    )
    tokenizer = get_tokenizer()
    chunks: List[Chunk] = []
    for index, text in enumerate(documents):
        nodes = parser.get_nodes_from_documents([Document(text=text)])
        leaf_texts = [leaf.get_content() for leaf in get_leaf_nodes(nodes)]
        chunks.extend(
            place_chunks(
                text,
                [leaf for leaf in leaf_texts if leaf.strip()],
                document=index,
                budget=budget,
                tokenizer=tokenizer,
            )
        )
    return chunks


def has_offsets(previous: Chunk, following: Chunk) -> bool:
    """Whether both sides of a boundary were placed in the document."""
    return previous.end is not None and following.start is not None


def carried_text(previous: Chunk, following: Chunk) -> str:
    """The text the splitter carried from ``previous`` into ``following``.

    Read from the source offsets when both chunks have them; otherwise fall
    back to the longest suffix/prefix string match.
    """
    if has_offsets(previous, following):
        shared = max(0, previous.end - following.start)
        return following.text[:shared]
    return overlap_text(previous.text, following.text)


def carried_chars(previous: Chunk, following: Chunk) -> int:
    """Characters carried across a boundary (see :func:`carried_text`)."""
    return len(carried_text(previous, following))


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile of ``values`` (any order); empty input gives 0."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def summarize_boundaries(carried: Sequence[int]) -> Dict[str, float]:
    """Describe the tokens carried across a set of boundaries."""
    boundaries = len(carried)
    empty = sum(1 for value in carried if value == 0)
    return {
        "boundaries": boundaries,
        "empty_boundaries": empty,
        "empty_pct": 100.0 * empty / boundaries if boundaries else 0.0,
        "mean_tokens": statistics.mean(carried) if carried else 0.0,
        "median_tokens": statistics.median(carried) if carried else 0.0,
        "p90_tokens": percentile(carried, 0.9),
    }


def measure(
    documents: Sequence[str],
    *,
    chunk_size: int,
    parent_chunk_size: int,
    overlap: int,
) -> Dict[str, float]:
    """Chunk ``documents`` at one setting and describe the boundaries it produces."""
    from llama_index.core.utils import get_tokenizer

    tokenizer = get_tokenizer()
    chunks = split_documents(
        documents,
        chunk_size=chunk_size,
        parent_chunk_size=parent_chunk_size,
        overlap=overlap,
    )
    pairs = [
        (previous, following)
        for previous, following in zip(chunks, chunks[1:])
        if previous.document == following.document
    ]
    carried = [len(tokenizer(carried_text(a, b))) for a, b in pairs]
    base_tokens = sum(len(tokenizer(document)) for document in documents)
    chunked_tokens = sum(len(tokenizer(chunk.text)) for chunk in chunks)

    row: Dict[str, float] = {
        "overlap": overlap,
        "chunk_size": chunk_size,
        "parent_chunk_size": parent_chunk_size,
        "documents": len(documents),
        "chunks": len(chunks),
    }
    row.update(summarize_boundaries(carried))
    row["fallback_boundaries"] = sum(1 for a, b in pairs if not has_offsets(a, b))
    row["index_inflation_pct"] = (
        100.0 * (chunked_tokens - base_tokens) / base_tokens if base_tokens else 0.0
    )
    return row


def format_table(rows: Sequence[Dict[str, float]]) -> str:
    """Render the sweep as a fixed-width table."""
    header = (
        f"{'overlap':>7} {'chunks':>7} {'empty boundaries':>18} "
        f"{'mean tok':>9} {'median':>7} {'p90':>5} {'index inflation':>16}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['overlap']:>7} {row['chunks']:>7} "
            f"{row['empty_boundaries']:>6}/{row['boundaries']:<5} "
            f"({row['empty_pct']:4.1f}%) {row['mean_tokens']:>9.1f} "
            f"{row['median_tokens']:>7.0f} {row['p90_tokens']:>5} "
            f"{row['index_inflation_pct']:>15.1f}%"
        )
    for row in rows:
        if row["fallback_boundaries"]:
            lines.append(
                f"overlap {row['overlap']}: {row['fallback_boundaries']} boundaries "
                "measured by string matching (chunk not emitted verbatim)"
            )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the overlap the ingest's sentence chunking actually carries "
            "across chunk boundaries, for a sweep of chunk_overlap values."
        )
    )
    parser.add_argument(
        "text_files",
        nargs="+",
        type=Path,
        help=(
            "Text file(s) of real corpus content, one document per NUL-terminated "
            "record (psql -0; see the module docstring)."
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Child chunk size in tokens (default: {DEFAULT_CHUNK_SIZE}).",
    )
    parser.add_argument(
        "--parent-chunk-size",
        type=int,
        default=DEFAULT_PARENT_CHUNK_SIZE,
        help=f"Parent chunk size in tokens (default: {DEFAULT_PARENT_CHUNK_SIZE}).",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        action="append",
        dest="overlaps",
        help=(
            "chunk_overlap value to measure; repeat to sweep "
            f"(default: {' '.join(str(o) for o in DEFAULT_OVERLAPS)})."
        ),
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Also write the rows to this path as JSON.",
    )
    args = parser.parse_args(argv)

    documents: List[str] = []
    for path in args.text_files:
        if not path.is_file():
            print(f"error: not a file: {path}", file=sys.stderr)
            return 1
        documents.extend(
            split_records(path.read_text(encoding="utf-8", errors="replace"))
        )
    if not documents:
        print("error: no usable text in the given file(s)", file=sys.stderr)
        return 1

    overlaps = args.overlaps or list(DEFAULT_OVERLAPS)
    rows = [
        measure(
            documents,
            chunk_size=args.chunk_size,
            parent_chunk_size=args.parent_chunk_size,
            overlap=overlap,
        )
        for overlap in sorted(set(overlaps))
    ]

    chars = sum(len(document) for document in documents)
    print(
        f"corpus: {len(documents)} documents, {chars} chars, "
        f"chunk_size={args.chunk_size}, parent_chunk_size={args.parent_chunk_size}\n"
    )
    print(format_table(rows))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
